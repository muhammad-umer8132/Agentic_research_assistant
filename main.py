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
    """
    duckduckgo-search library — thread pool mein chalao taake async loop block na ho.
    Timeout safe_search mein handle hota hai.
    """
    loop = asyncio.get_event_loop()
    def _sync():
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
            return [r['href'] for r in results if r.get('href','').startswith('http')]
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
    async with httpx.AsyncClient(timeout=12, follow_redirects=True) as client:
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


# ── LLM (Sirf Gemma) ──────────────────────────────────────────────────────────
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
                yield f"Error: LLM status {resp.status_code}. Dobara try karein."
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

    # 2. Parallel search — 5 engines, 20s timeout each
    #    Koi bhi fail ho, baaki chalte rahenge
    search_tasks = [
        safe_search("DuckDuckGo", search_ddg(q, 8),    timeout=25),  
        safe_search("Google",     search_google(q, 8),  timeout=35),
        #safe_search("Bing",       search_bing(q, 8),    timeout=35),
        #safe_search("Brave",      search_brave(q, 6),   timeout=15),
        safe_search("Yahoo",      search_yahoo(q, 6),   timeout=15),
    ]
    results_lists = await asyncio.gather(*search_tasks)

    all_urls = []
    for res in results_lists:
        all_urls.extend(res)

    seen = set()
    unique_urls = []
    for url in all_urls:
        if url not in seen and url.startswith("http"):
            seen.add(url)
            unique_urls.append(url)

    urls = unique_urls[:8]
    logger.info(f"🔗 Unique URLs to scrape: {len(urls)}")

    if not urls:
        return {"error": "Kisi bhi search engine se results nahi aaye"}

    # 3. Scrape
    contents = await scrape_urls(urls)
    if not contents:
        return {"error": "Content scrape nahi ho saka"}

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