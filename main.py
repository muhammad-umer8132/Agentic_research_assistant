import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
import os
import json
import logging
import base64
from contextlib import asynccontextmanager
from urllib.parse import quote, unquote
import httpx
import redis.asyncio as aioredis
from fastapi import FastAPI, Query
from fastapi.responses import StreamingResponse
from dotenv import load_dotenv
from ddgs import DDGS
from bs4 import BeautifulSoup

from scraper import scrape_urls, init_selenium, close_selenium

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Redis ──────────────────────────────────────────────────────────────────────
redis_client = None

async def init_redis():
    global redis_client
    try:
        redis_client = await aioredis.from_url(
            os.getenv("REDIS_URL", "redis://localhost:6379"), decode_responses=True
        )
        await redis_client.ping()
        logger.info("✅ Redis connected")
    except Exception as e:
        logger.warning(f"Redis connect failed: {e}. Cache disabled.")
        redis_client = None

async def close_redis():
    if redis_client:
        await redis_client.close()

async def get_cache(query: str):
    if not redis_client:
        return None
    key = f"search:{query.lower().strip()}"
    data = await redis_client.get(key)
    return json.loads(data) if data else None

async def set_cache(query: str, result: dict, ttl=3600):
    if not redis_client:
        return
    key = f"search:{query.lower().strip()}"
    await redis_client.setex(key, ttl, json.dumps(result, ensure_ascii=False))

# ── Lifespan ───────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_redis()
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, init_selenium)
    yield
    await loop.run_in_executor(None, close_selenium)
    await close_redis()

app = FastAPI(lifespan=lifespan)

# ── Shared HTTP headers ────────────────────────────────────────────────────────
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "DNT": "1",
}

# ── Safe wrapper ───────────────────────────────────────────────────────────────
async def safe_search(name: str, coro, timeout: int = 20) -> list:
    """Timeout aur exceptions dono handle karta hai — kabhi crash nahi karta."""
    try:
        result = await asyncio.wait_for(coro, timeout=timeout)
        if isinstance(result, list):
            logger.info(f"✅ {name}: {len(result)} URLs")
            return result
        return []
    except asyncio.TimeoutError:
        logger.warning(f"⏱️ {name}: timeout ({timeout}s) — skip")
        return []
    except Exception as e:
        logger.warning(f"❌ {name}: {type(e).__name__}: {e}")
        return []

# ── Search Engines ─────────────────────────────────────────────────────────────

async def search_ddg(query: str, max_results: int = 8) -> list:
    """DuckDuckGo — explicit backend to avoid internal cascading timeouts."""
    loop = asyncio.get_event_loop()
    def _sync():
        try:
            with DDGS() as ddgs:
                # ✅ Explicit backend: sirf DDG aur HTML, no Yandex/Brave fallback
                results = list(ddgs.text(
                    query, 
                    max_results=max_results,
                    backend="duckduckgo, yahoo",   # ← only these
                ))
                return [r['href'] for r in results if r.get('href','').startswith('http')]
        except Exception as e:
            logger.debug(f"DDG sync error: {e}")
            return []
    return await loop.run_in_executor(None, _sync)


async def search_google(query: str, max_results: int = 8) -> list:
    from scraper import _search_scraper
    url = f"https://www.google.com/search?q={quote(query)}&num={max_results}&hl=en"
    
    loop = asyncio.get_event_loop()
    html = await loop.run_in_executor(
        None, lambda: _search_scraper.get_page_html(url, wait_seconds=2.0)
    )
    if not html:
        return []
    
    soup = BeautifulSoup(html, 'html.parser')
    links = []
    
    # Primary selector (browser-confirmed)
    for a in soup.select('a[jsname="UWckNb"]'):
        href = a.get('href', '')
        if href.startswith('http') and 'google.com' not in href:
            links.append(href)
    
    # Fallback
    if not links:
        for a in soup.select('div.yuRUbf a[href^="http"]'):
            href = a.get('href', '')
            if 'google.com' not in href:
                links.append(href)
    
    logger.info(f"✅ Google (Selenium): {len(links)} URLs")
    return list(dict.fromkeys(links))[:max_results]


async def search_bing(query: str, max_results: int = 8) -> list:
    from scraper import _search_scraper
    url = f"https://www.bing.com/search?q={quote(query)}&count={max_results}"
    
    loop = asyncio.get_event_loop()
    html = await loop.run_in_executor(
        None, lambda: _search_scraper.get_page_html(url, wait_seconds=2.0)
    )
    if not html:
        return []
    
    soup = BeautifulSoup(html, 'html.parser')
    links = []
    
    # Method 1: /ck/a decode
    for a in soup.select('a[href*="/ck/a"]'):
        href = a.get('href', '')
        if 'u=a1' not in href:
            continue
        try:
            u_part = href.split('u=a1')[1].split('&')[0]
            # Padding fix
            u_part += '=' * (4 - len(u_part) % 4)
            real = base64.b64decode(u_part).decode('utf-8')
            if real.startswith('http'):
                links.append(real)
        except Exception:
            continue
    
    # Fallback: h2 direct links
    if not links:
        for a in soup.select('h2 a[href^="http"]'):
            href = a['href']
            if 'bing.com' not in href and 'microsoft.com' not in href:
                links.append(href)
    
    logger.info(f"✅ Bing (Selenium): {len(links)} URLs")
    return list(dict.fromkeys(links))[:max_results]


async def search_brave(query: str, max_results: int = 6) -> list:
    from scraper import _search_scraper
    url = f"https://search.brave.com/search?q={quote(query)}&source=web"
    
    loop = asyncio.get_event_loop()
    html = await loop.run_in_executor(
        None, lambda: _search_scraper.get_page_html(url, wait_seconds=2.0)
    )
    if not html:
        return []
    
    soup = BeautifulSoup(html, 'html.parser')
    links = []
    
    for a in soup.select('a.result-header, [data-type="web"] a[href^="http"]'):
        href = a.get('href', '')
        if href.startswith('http') and 'brave.com' not in href:
            links.append(href)
    
    logger.info(f"✅ Brave (Selenium): {len(links)} URLs")
    return list(dict.fromkeys(links))[:max_results]


async def search_yahoo(query: str, max_results: int = 6) -> list:
    """
    Yahoo Search — extra fallback engine.
    Yahoo redirect links decode karke real URLs nikalta hai.
    """
    url = f"https://search.yahoo.com/search?p={quote(query)}&n={max_results}"
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        resp = await client.get(url, headers=BROWSER_HEADERS)
        if resp.status_code != 200:
            logger.warning(f"Yahoo status {resp.status_code} — skip")
            return []

    soup = BeautifulSoup(resp.text, 'html.parser')
    links = []

    for a in soup.find_all('a', href=True):
        href = a['href']
        # Yahoo wraps links: /url?q=https://...
        if '/url?q=' in href:
            real = href.split('/url?q=')[1].split('&')[0]
            real = unquote(real)
            if real.startswith('http') and 'yahoo' not in real:
                links.append(real)
        elif href.startswith('http') and 'yahoo' not in href and 'yimg' not in href:
            links.append(href)

    return list(dict.fromkeys(links))[:max_results]

# ── TIER 2: Backup Search Engines ──────────────────────────────────────────────

async def search_startpage(query: str, max_results: int = 8) -> list:
    """Startpage — Google results without tracking."""
    url = f"https://www.startpage.com/sp/search?query={quote(query)}"
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            resp = await client.get(url, headers=BROWSER_HEADERS)
            if resp.status_code != 200:
                logger.warning(f"Startpage status {resp.status_code} — skip")
                return []
        soup = BeautifulSoup(resp.text, 'html.parser')
        links = []
        for a in soup.select('a.w-gl__result-title, a.result-link, h3 a[href^="http"]'):
            href = a.get('href', '')
            if href.startswith('http') and 'startpage.com' not in href:
                links.append(href)
        return list(dict.fromkeys(links))[:max_results]
    except Exception as e:
        logger.debug(f"Startpage error: {e}")
        return []


async def search_searxng(query: str, max_results: int = 10) -> list:
    """SearXNG — meta-search aggregator (10+ engines)."""
    instances = [
        "https://searx.be",
        "https://priv.au",
        "https://searx.tiekoetter.com",
        "https://search.brave4u.com",
        "https://searx.work",
    ]
    for instance in instances:
        try:
            url = f"{instance}/search?q={quote(query)}&format=json&language=en"
            async with httpx.AsyncClient(timeout=8, follow_redirects=True) as client:
                resp = await client.get(url, headers=BROWSER_HEADERS)
                if resp.status_code != 200:
                    continue
                data = resp.json()
                links = []
                for r in data.get("results", [])[:max_results]:
                    link = r.get("url", "")
                    if link.startswith("http"):
                        links.append(link)
                if links:
                    logger.info(f"✅ SearXNG ({instance.split('//')[1]}): {len(links)} URLs")
                    return list(dict.fromkeys(links))[:max_results]
        except Exception:
            continue
    logger.warning("All SearXNG instances failed")
    return []


async def search_mojeek(query: str, max_results: int = 8) -> list:
    """Mojeek — independent index, separate from Google/Bing."""
    url = f"https://www.mojeek.com/search?q={quote(query)}"
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            resp = await client.get(url, headers=BROWSER_HEADERS)
            if resp.status_code != 200:
                return []
        soup = BeautifulSoup(resp.text, 'html.parser')
        links = []
        for a in soup.select('a.ob, h2 a[href^="http"], .results-standard a[href^="http"]'):
            href = a.get('href', '')
            if href.startswith('http') and 'mojeek.com' not in href:
                links.append(href)
        return list(dict.fromkeys(links))[:max_results]
    except Exception as e:
        logger.debug(f"Mojeek error: {e}")
        return []


async def search_qwant(query: str, max_results: int = 8) -> list:
    """Qwant — Bing-backed, JSON API."""
    url = f"https://api.qwant.com/v3/search/web?q={quote(query)}&count={max_results}&locale=en_us&safesearch=1"
    headers = {**BROWSER_HEADERS, "Accept": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=8, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code != 200:
                return []
            data = resp.json()
            items = data.get("data", {}).get("result", {}).get("items", {}).get("mainline", [])
            links = []
            for item in items:
                if item.get("type") == "web":
                    for entry in item.get("items", []):
                        link = entry.get("url", "")
                        if link.startswith("http"):
                            links.append(link)
            return list(dict.fromkeys(links))[:max_results]
    except Exception as e:
        logger.debug(f"Qwant error: {e}")
        return []


async def search_ecosia(query: str, max_results: int = 8) -> list:
    """Ecosia — eco-friendly, Bing-backed."""
    url = f"https://www.ecosia.org/search?q={quote(query)}"

    enhanced_headers = {
        **BROWSER_HEADERS,
        "Referer": "https://www.ecosia.org/",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Cache-Control": "max-age=0",
    }
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            resp = await client.get(url, headers=BROWSER_HEADERS)
            if resp.status_code != 200:
                return []
        soup = BeautifulSoup(resp.text, 'html.parser')
        links = []
        for a in soup.select('a.result__title, a.result-url, a[data-test-id="result-link"]'):
            href = a.get('href', '')
            if href.startswith('http') and 'ecosia.org' not in href:
                links.append(href)
        if not links:
            for a in soup.select('article a[href^="http"]'):
                href = a['href']
                if 'ecosia.org' not in href:
                    links.append(href)
        return list(dict.fromkeys(links))[:max_results]
    except Exception as e:
        logger.debug(f"Ecosia error: {e}")
        return []
    
# ── Tiered Search Logic ────────────────────────────────────────────────────────

async def get_urls_tiered(q: str, min_required: int = 5) -> list:
    """
    Tier 1: Google, DuckDuckGo, Yahoo, Brave, Bing (parallel)
    Tier 2: Startpage, SearXNG, Mojeek, Qwant, Ecosia (only if Tier 1 weak)
    """
    all_urls = []
    seen = set()
    
    def add_urls(new_urls):
        for url in new_urls:
            if url not in seen and url.startswith("http"):
                seen.add(url)
                all_urls.append(url)
    
    # ── TIER 1 ──
    logger.info("🎯 TIER 1: Google, DuckDuckGo, Yahoo, Brave, Bing")
    tier1 = await asyncio.gather(
        safe_search("Google",     search_google(q, 10), timeout=25),
        safe_search("DuckDuckGo", search_ddg(q, 8),     timeout=20),
        safe_search("Yahoo",      search_yahoo(q, 8),   timeout=15),
        safe_search("Brave",      search_brave(q, 8),   timeout=20),
        safe_search("Bing",       search_bing(q, 10),   timeout=25),
    )
    for urls in tier1:
        add_urls(urls)
    
    if len(all_urls) >= min_required:
        logger.info(f"✅ Tier 1 sufficient: {len(all_urls)} URLs")
        return all_urls
    
    # ── TIER 2 ──
    logger.warning(f"⚠️ Tier 1 weak ({len(all_urls)} URLs) — trying Tier 2")
    tier2 = await asyncio.gather(
        safe_search("Startpage", search_startpage(q, 8), timeout=15),
        safe_search("SearXNG",   search_searxng(q, 10),  timeout=20),
        safe_search("Mojeek",    search_mojeek(q, 8),    timeout=15),
        safe_search("Qwant",     search_qwant(q, 8),     timeout=15),
        safe_search("Ecosia",    search_ecosia(q, 8),    timeout=15),
    )
    for urls in tier2:
        add_urls(urls)
    
    logger.info(f"🏁 Final from all tiers: {len(all_urls)} URLs")
    return all_urls
# ── LLM (Gemma) ──────────────────────────────────────────────────────────
async def ask_llm(prompt: str):
    base_url = os.getenv("LLM_BASE_URL", "https://gemma4.limeox.org/v1/chat/completions")
    model    = os.getenv("LLM_MODEL", "gemma4")
    logger.info(f"🤖 Gemma call → {base_url}")

    json_data = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": True,
        "max_tokens": 1200,
        "temperature": 0.4,
    }
    headers = {"Content-Type": "application/json", "User-Agent": "ResearchBot/1.0"}

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=10.0)) as client:
            resp = await client.post(base_url, json=json_data, headers=headers)
            logger.info(f"📡 LLM status: {resp.status_code}")
            if resp.status_code != 200:
                yield f"Error: LLM status {resp.status_code}. try again."
                return

            token_count = 0
            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    data = line[6:]
                    if data == "[DONE]":
                        logger.info(f"✅ LLM done — {token_count} tokens")
                        break
                    try:
                        chunk = json.loads(data)
                        token = chunk['choices'][0]['delta'].get('content', '')
                        if token:
                            token_count += 1
                            yield token
                    except Exception as e:
                        logger.warning(f"⚠️ Parse: {e}")
                        continue

            if token_count == 0:
                yield "Error: LLM se koi response nahi aaya. Dobara try karein."

    except httpx.TimeoutException:
        yield "Error: LLM timeout Try again"
    except Exception as e:
        yield f"Error: {e}"


# ── Prompt ─────────────────────────────────────────────────────────────────────
def build_prompt(query: str, contents: dict) -> str:
    sources_text = ""
    for i, (url, text) in enumerate(contents.items(), start=1):
        cleaned = " ".join(text.split())
        snippet = cleaned[:2000]
        sources_text += f"[{i}] SOURCE: {url}\nCONTENT: {snippet}\n\n"

    return (
        f"You are a research assistant. Answer the following question in detail "
        f"using ONLY the provided sources.\n\n"
        f"QUESTION: {query}\n\n"
        f"=== SOURCES ===\n{sources_text}"
        f"=== END SOURCES ===\n\n"
        f"INSTRUCTIONS:\n"
        f"- Write a **comprehensive, well-structured answer** with headings, bullet points, and paragraphs.\n"
        f"- Cover ALL important aspects: background, key facts, details, implications.\n"
        f"- Cite every claim with [1], [2], etc.\n"
        f"- Minimum 3 sections with headings.\n"
        f"- Do NOT repeat the question. Do NOT list raw sources at the end.\n"
        f"- Write at least 300 words.\n"
    )


# ── API Route ──────────────────────────────────────────────────────────────────
@app.get("/search")
async def search_endpoint(q: str = Query(...)):

    # 1. Cache check
    cached = await get_cache(q)
    if cached:
        async def cached_streamer():
            summary = cached.get('summary', '')
            sources = cached.get('sources', [])
            yield "data: Scraping done, generating summary...\n\n"
            for line in summary.split('\n'):
                if line.strip():
                    yield f"data: {line}\n\n"
            yield f"data: {json.dumps({'event':'final','sources':sources,'summary':summary})}\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(cached_streamer(), media_type="text/event-stream")

    # 2. Tiered search (10 engines total)
    all_urls = await get_urls_tiered(q, min_required=5)
    urls = all_urls[:8]
    logger.info(f"🔗 Unique URLs to scrape: {len(urls)}")

    # 3. Saare engines fail — graceful error
    if not urls:
        async def error_streamer():
            yield "data: ⚠️ All search engines unavailable right now.\n\n"
            yield "data: Please try again in a moment.\n\n"
            yield f"event: final\ndata: {json.dumps({'sources':[]})}\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(error_streamer(), media_type="text/event-stream")

    # 3. Scrape
    contents = await scrape_urls(urls)
    if not contents:
        async def scrape_error_streamer():
            yield "data: Found URLs but couldn't read content.\n\n"
            yield "data: ⚠️ Websites blocked our requests. Try again.\n\n"
            yield f"event: final\ndata: {json.dumps({'sources':urls[:5]})}\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(scrape_error_streamer(), media_type="text/event-stream")

    logger.info(f"📄 Scraped {len(contents)} pages")
    prompt = build_prompt(q, contents)
    logger.info(f"📝 Prompt: {len(prompt)} chars (~{len(prompt)//4} tokens)")

    # 4. Stream + cache
    async def streamer():
        full_summary = ""
        buffer = ""
        yield "data: Scraping done, generating summary...\n\n"
        try:
            async for token in ask_llm(prompt):
                full_summary += token
                buffer += token
                if '\n' in buffer:
                    lines = buffer.split('\n')
                    buffer = lines.pop()
                    for line in lines:
                        if line.strip():
                            yield f"data: {line}\n\n"
            if buffer.strip():
                yield f"data: {buffer}\n\n"
        except Exception as e:
            logger.error(f"❌ Streaming error: {e}")
            yield f"data: Error: {e}\n\n"
        
        yield f"data: {json.dumps({'event':'final','sources':list(contents.keys())})}\n\n"
        if full_summary.strip():
            await set_cache(q, {"query": q, "summary": full_summary, "sources": list(contents.keys())})
        yield "data: [DONE]\n\n"

    return StreamingResponse(streamer(), media_type="text/event-stream")