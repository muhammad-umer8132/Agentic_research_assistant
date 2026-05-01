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

# ── Selenium Health Tracker ─────────────────────────────────────────────────────
_selenium_failures = 0
_SELENIUM_MAX_FAILURES = 3
_selenium_lock = asyncio.Lock()

async def restart_selenium_if_needed():
    """Agar Selenium repeatedly fail ho, toh restart karo."""
    global _selenium_failures
    async with _selenium_lock:
        if _selenium_failures >= _SELENIUM_MAX_FAILURES:
            logger.warning("🔄 Restarting Selenium driver...")
            loop = asyncio.get_running_loop()
            try:
                await loop.run_in_executor(None, close_selenium)
                await asyncio.sleep(1)
                await loop.run_in_executor(None, init_selenium)
                _selenium_failures = 0
                logger.info("✅ Selenium restarted")
            except Exception as e:
                logger.error(f"❌ Selenium restart failed: {e}")

def mark_selenium_failure():
    global _selenium_failures
    _selenium_failures += 1

def mark_selenium_success():
    global _selenium_failures
    _selenium_failures = 0

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

# ── HTTP client with browser-like behavior ─────────────────────────────────────
def get_realistic_headers(referer: str = None) -> dict:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none" if not referer else "same-origin",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
        "Cache-Control": "max-age=0",
        "DNT": "1",
    }
    if referer:
        headers["Referer"] = referer
    return headers

# ── Safe wrapper ───────────────────────────────────────────────────────────────
async def safe_search(name: str, coro, timeout: int = 20) -> list:
    is_selenium = name in ("Google", "Bing", "Brave")
    try:
        result = await asyncio.wait_for(coro, timeout=timeout)
        if isinstance(result, list):
            logger.info(f"✅ {name}: {len(result)} URLs")
            if is_selenium and len(result) > 0:
                mark_selenium_success()
            elif is_selenium:
                mark_selenium_failure()
            return result
        return []
    except asyncio.TimeoutError:
        logger.warning(f"⏱️ {name}: timeout ({timeout}s) — skip")
        if is_selenium:
            mark_selenium_failure()
        return []
    except Exception as e:
        logger.warning(f"❌ {name}: {type(e).__name__}: {e}")
        if is_selenium:
            mark_selenium_failure()
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
                    backend="duckduckgo",   # ← only these
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
    """Yahoo — RU= encoded redirect decode karta hai."""
    url = f"https://search.yahoo.com/search?p={quote(query)}&n={max_results}"
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        resp = await client.get(url, headers=BROWSER_HEADERS)
        if resp.status_code != 200:
            logger.warning(f"Yahoo status {resp.status_code} — skip")
            return []

    soup = BeautifulSoup(resp.text, 'html.parser')
    links = []

    # Yahoo wraps URLs as: .../RU=https%3a%2f%2fexample.com/RK=...
    for a in soup.select('h3.title a, h3 a[href], a.fz-ms[href]'):
        href = a.get('href', '')
        if 'RU=' in href:
            try:
                ru_part = href.split('RU=')[1].split('/RK=')[0]
                real = unquote(ru_part)
                if real.startswith('http') and 'yahoo.com' not in real:
                    links.append(real)
            except Exception:
                continue
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
    """SearXNG — JSON validation added."""
    instances = [
        "https://searx.tiekoetter.com",
        "https://searx.be",
        "https://priv.au",
        "https://search.brave4u.com",
        "https://searx.work",
    ]
    for instance in instances:
        try:
            url = f"{instance}/search?q={quote(query)}&format=json&language=en"
            async with httpx.AsyncClient(timeout=8, follow_redirects=False) as client:
                resp = await client.get(url, headers=BROWSER_HEADERS)
                if resp.status_code != 200:
                    continue
                # ✅ Verify JSON content-type (302 redirects pe HTML aata hai)
                if 'application/json' not in resp.headers.get('content-type', ''):
                    continue
                try:
                    data = resp.json()
                except Exception:
                    continue
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
    """Mojeek with session warmup."""
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True, http2=True) as client:
            # ✅ Warmup: homepage visit karke cookies acquire karo
            await client.get("https://www.mojeek.com/", headers=get_realistic_headers())
            await asyncio.sleep(0.5)  # Human-like delay
            
            url = f"https://www.mojeek.com/search?q={quote(query)}"
            resp = await client.get(url, headers=get_realistic_headers("https://www.mojeek.com/"))
            if resp.status_code != 200:
                logger.debug(f"Mojeek status {resp.status_code}")
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
    """Ecosia with session warmup."""
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True, http2=True) as client:
            # ✅ Warmup
            await client.get("https://www.ecosia.org/", headers=get_realistic_headers())
            await asyncio.sleep(0.5)
            
            url = f"https://www.ecosia.org/search?q={quote(query)}"
            resp = await client.get(url, headers=get_realistic_headers("https://www.ecosia.org/"))
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

# ════════════════════════════════════════════════════════════════════════════════
# TIER 3: Yandex, Marginalia, You.com, Swisscows, Baidu
# ════════════════════════════════════════════════════════════════════════════════

async def search_yandex(query: str, max_results: int = 8) -> list:
    """Yandex — independent Russian index, global coverage."""
    url = f"https://yandex.com/search/?text={quote(query)}&lr=10393"
    try:
        async with httpx.AsyncClient(timeout=12, follow_redirects=True) as client:
            resp = await client.get(url, headers=BROWSER_HEADERS)
            if resp.status_code != 200:
                logger.warning(f"Yandex status {resp.status_code} — skip")
                return []
        soup = BeautifulSoup(resp.text, 'html.parser')
        links = []
        for a in soup.select('a.Link.OrganicTitle-Link, a.organic__url, h2 a[href^="http"]'):
            href = a.get('href', '')
            if href.startswith('http') and 'yandex' not in href:
                links.append(href)
        return list(dict.fromkeys(links))[:max_results]
    except Exception as e:
        logger.debug(f"Yandex error: {e}")
        return []


async def search_marginalia(query: str, max_results: int = 8) -> list:
    """Marginalia — independent UK crawler. Has public JSON API."""
    url = f"https://api.marginalia.nu/public/search/{quote(query)}?count={max_results}"
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            resp = await client.get(url, headers={"Accept": "application/json"})
            if resp.status_code != 200:
                # Fallback to HTML scrape
                url2 = f"https://search.marginalia.nu/search?query={quote(query)}"
                resp = await client.get(url2, headers=BROWSER_HEADERS)
                if resp.status_code != 200:
                    return []
                soup = BeautifulSoup(resp.text, 'html.parser')
                links = []
                for a in soup.select('div.search-result a[href^="http"], h2 a[href^="http"]'):
                    href = a.get('href', '')
                    if href.startswith('http') and 'marginalia' not in href:
                        links.append(href)
                return list(dict.fromkeys(links))[:max_results]
            data = resp.json()
            links = [r.get("url", "") for r in data.get("results", [])
                     if r.get("url", "").startswith("http")]
            return list(dict.fromkeys(links))[:max_results]
    except Exception as e:
        logger.debug(f"Marginalia error: {e}")
        return []


async def search_youcom(query: str, max_results: int = 8) -> list:
    """You.com — AI-powered, scraping HTML."""
    url = f"https://you.com/search?q={quote(query)}&tbm=youchat"
    try:
        async with httpx.AsyncClient(timeout=12, follow_redirects=True) as client:
            resp = await client.get(url, headers=BROWSER_HEADERS)
            if resp.status_code != 200:
                return []
        soup = BeautifulSoup(resp.text, 'html.parser')
        links = []
        for a in soup.select('a[data-testid="web-result-title"], article a[href^="http"]'):
            href = a.get('href', '')
            if href.startswith('http') and 'you.com' not in href:
                links.append(href)
        return list(dict.fromkeys(links))[:max_results]
    except Exception as e:
        logger.debug(f"You.com error: {e}")
        return []


async def search_swisscows(query: str, max_results: int = 8) -> list:
    """Swisscows — Swiss, family-friendly, partial own index."""
    url = f"https://swisscows.com/en/web?query={quote(query)}"
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            resp = await client.get(url, headers=BROWSER_HEADERS)
            if resp.status_code != 200:
                return []
        soup = BeautifulSoup(resp.text, 'html.parser')
        links = []
        for a in soup.select('article.web-result a.title, a[data-test-id="web-link"]'):
            href = a.get('href', '')
            if href.startswith('http') and 'swisscows' not in href:
                links.append(href)
        return list(dict.fromkeys(links))[:max_results]
    except Exception as e:
        logger.debug(f"Swisscows error: {e}")
        return []


async def search_baidu(query: str, max_results: int = 8) -> list:
    """Baidu — Chinese giant, English queries supported."""
    url = f"https://www.baidu.com/s?wd={quote(query)}&rn={max_results}"
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            resp = await client.get(url, headers=BROWSER_HEADERS)
            if resp.status_code != 200:
                return []
        soup = BeautifulSoup(resp.text, 'html.parser')
        links = []
        # Baidu uses redirect URLs — first try direct, then mu attribute
        for h3 in soup.select('h3.t a, div.result a[href]'):
            mu = h3.get('mu', '')  # ✅ original URL hidden in mu attribute
            href = h3.get('href', '')
            if mu and mu.startswith('http') and 'baidu' not in mu:
                links.append(mu)
            elif href.startswith('http') and 'baidu.com' not in href:
                links.append(href)
        return list(dict.fromkeys(links))[:max_results]
    except Exception as e:
        logger.debug(f"Baidu error: {e}")
        return []


# ════════════════════════════════════════════════════════════════════════════════
# TIER 4: Stract, Presearch, Metager, LibreX, Whoogle
# ════════════════════════════════════════════════════════════════════════════════

async def search_stract(query: str, max_results: int = 8) -> list:
    """Stract — open-source independent crawler with JSON API."""
    url = "https://stract.com/beta/api/search"
    payload = {"query": query, "numResults": max_results}
    try:
        async with httpx.AsyncClient(timeout=8, follow_redirects=True) as client:
            resp = await client.post(url, json=payload,
                                     headers={**BROWSER_HEADERS, "Content-Type": "application/json"})
            if resp.status_code != 200:
                return []
            data = resp.json()
            links = []
            for r in data.get("webpages", []):
                link = r.get("url", "")
                if link.startswith("http"):
                    links.append(link)
            return list(dict.fromkeys(links))[:max_results]
    except Exception as e:
        logger.debug(f"Stract error: {e}")
        return []


async def search_presearch(query: str, max_results: int = 8) -> list:
    """Presearch — decentralized search."""
    url = f"https://presearch.com/search?q={quote(query)}"
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            resp = await client.get(url, headers=BROWSER_HEADERS)
            if resp.status_code != 200:
                return []
        soup = BeautifulSoup(resp.text, 'html.parser')
        links = []
        for a in soup.select('a.result-link, div.result a[href^="http"], h3 a[href^="http"]'):
            href = a.get('href', '')
            if href.startswith('http') and 'presearch' not in href:
                links.append(href)
        return list(dict.fromkeys(links))[:max_results]
    except Exception as e:
        logger.debug(f"Presearch error: {e}")
        return []


async def search_metager(query: str, max_results: int = 8) -> list:
    """Metager — German meta-search."""
    url = f"https://metager.org/meta/meta.ger3?eingabe={quote(query)}"
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            resp = await client.get(url, headers=BROWSER_HEADERS)
            if resp.status_code != 200:
                return []
        soup = BeautifulSoup(resp.text, 'html.parser')
        links = []
        for a in soup.select('h2.result-title a, a.result-link, div.result a[href^="http"]'):
            href = a.get('href', '')
            if href.startswith('http') and 'metager' not in href:
                links.append(href)
        return list(dict.fromkeys(links))[:max_results]
    except Exception as e:
        logger.debug(f"Metager error: {e}")
        return []


async def search_librex(query: str, max_results: int = 8) -> list:
    """LibreX/LibreY — meta-search instances (try multiple)."""
    instances = [
        "https://search.davidovski.xyz",
        "https://librex.beparanoid.de",
        "https://lx.benike.me",
        "https://librex.retrowave.dev",
    ]
    for instance in instances:
        try:
            url = f"{instance}/api.php?q={quote(query)}&p=0&t=0"
            async with httpx.AsyncClient(timeout=6, follow_redirects=True) as client:
                resp = await client.get(url, headers={**BROWSER_HEADERS, "Accept": "application/json"})
                if resp.status_code != 200:
                    continue
                if 'application/json' not in resp.headers.get('content-type', ''):
                    continue
                try:
                    data = resp.json()
                except Exception:
                    continue
                links = []
                for r in data[:max_results] if isinstance(data, list) else []:
                    link = r.get("url", "")
                    if link.startswith("http"):
                        links.append(link)
                if links:
                    logger.info(f"✅ LibreX ({instance.split('//')[1]}): {len(links)} URLs")
                    return list(dict.fromkeys(links))[:max_results]
        except Exception:
            continue
    return []


async def search_whoogle(query: str, max_results: int = 8) -> list:
    """Whoogle — self-hosted Google proxy instances."""
    instances = [
        "https://whoogle.dcs0.hu",
        "https://search.albony.xyz",
        "https://whoogle.privacydev.net",
        "https://whoogle.ssrvodka.fr",
    ]
    for instance in instances:
        try:
            url = f"{instance}/search?q={quote(query)}"
            async with httpx.AsyncClient(timeout=6, follow_redirects=True) as client:
                resp = await client.get(url, headers=BROWSER_HEADERS)
                if resp.status_code != 200:
                    continue
            soup = BeautifulSoup(resp.text, 'html.parser')
            links = []
            for a in soup.select('div.yuRUbf a[href^="http"], a[href^="http"][rel="noopener"]'):
                href = a.get('href', '')
                if href.startswith('http') and 'whoogle' not in href and instance.split('//')[1] not in href:
                    links.append(href)
            if links:
                logger.info(f"✅ Whoogle ({instance.split('//')[1]}): {len(links)} URLs")
                return list(dict.fromkeys(links))[:max_results]
        except Exception:
            continue
    return []

    
# ── Tiered Search Logic ────────────────────────────────────────────────────────

async def get_urls_tiered(q: str, min_required: int = 5) -> list:
    """
    Tier 1: Google, DuckDuckGo, Yahoo, Brave, Bing
    Tier 2: Startpage, SearXNG, Mojeek, Qwant, Ecosia
    Tier 3: Yandex, Marginalia, You.com, Swisscows, Baidu
    Tier 4: Stract, Presearch, Metager, LibreX, Whoogle
    """
    # ✅ Pehle Selenium health check
    await restart_selenium_if_needed()
    
    all_urls = []
    seen = set()
    
    def add_urls(new_urls):
        for url in new_urls:
            if url not in seen and url.startswith("http"):
                seen.add(url)
                all_urls.append(url)
    
    # ── TIER 1 ──
    logger.info("🎯 TIER 1: Google, DuckDuckGo, Yahoo, Brave, Bing")
    
    # ✅ httpx engines parallel
    httpx_results = await asyncio.gather(
        safe_search("DuckDuckGo", search_ddg(q, 8),   timeout=15),
        safe_search("Yahoo",      search_yahoo(q, 8), timeout=12),
    )
    for urls in httpx_results:
        add_urls(urls)
    
    # ✅ Selenium engines SEQUENTIAL (shared driver, no parallel deadlock)
    if len(all_urls) < min_required:
        for name, search_fn, tmout in [
            ("Google", search_google, 20),
            ("Bing",   search_bing,   20),
            ("Brave",  search_brave,  18),
        ]:
            urls = await safe_search(name, search_fn(q, 10), timeout=tmout)
            add_urls(urls)
            if len(all_urls) >= min_required:
                break  # enough mil gaya, baaki skip
    
    if len(all_urls) >= min_required:
        logger.info(f"✅ Tier 1 sufficient: {len(all_urls)} URLs")
        return all_urls
    
    # ── TIER 2 ──
    logger.warning(f"⚠️ Tier 1 weak ({len(all_urls)}) — Tier 2")
    tier2 = await asyncio.gather(
        safe_search("Startpage", search_startpage(q, 8), timeout=12),
        safe_search("SearXNG",   search_searxng(q, 10),  timeout=15),
        safe_search("Mojeek",    search_mojeek(q, 8),    timeout=12),
        safe_search("Qwant",     search_qwant(q, 8),     timeout=12),
        safe_search("Ecosia",    search_ecosia(q, 8),    timeout=12),
    )
    for urls in tier2:
        add_urls(urls)
    
    if len(all_urls) >= min_required:
        logger.info(f"✅ Tier 2 sufficient: {len(all_urls)} URLs")
        return all_urls
    
    # ── TIER 3 ──
    logger.warning(f"⚠️ Tier 2 weak ({len(all_urls)}) — Tier 3: Yandex, Marginalia, You.com, Swisscows, Baidu")
    tier3 = await asyncio.gather(
        safe_search("Yandex",     search_yandex(q, 8),     timeout=12),
        safe_search("Marginalia", search_marginalia(q, 8), timeout=10),
        safe_search("You.com",    search_youcom(q, 8),     timeout=12),
        safe_search("Swisscows",  search_swisscows(q, 8),  timeout=10),
        safe_search("Baidu",      search_baidu(q, 8),      timeout=10),
    )
    for urls in tier3:
        add_urls(urls)
    
    if len(all_urls) >= min_required:
        logger.info(f"✅ Tier 3 sufficient: {len(all_urls)} URLs")
        return all_urls
    
    # ── TIER 4 ──
    logger.warning(f"⚠️ Tier 3 weak ({len(all_urls)}) — Tier 4: Stract, Presearch, Metager, LibreX, Whoogle")
    tier4 = await asyncio.gather(
        safe_search("Stract",    search_stract(q, 8),    timeout=8),
        safe_search("Presearch", search_presearch(q, 8), timeout=10),
        safe_search("Metager",   search_metager(q, 8),   timeout=10),
        safe_search("LibreX",    search_librex(q, 8),    timeout=8),
        safe_search("Whoogle",   search_whoogle(q, 8),   timeout=8),
    )
    for urls in tier4:
        add_urls(urls)
    
    logger.info(f"🏁 Final from all 4 tiers: {len(all_urls)} URLs")
    return all_urls
# ── LLM (Gemma) ──────────────────────────────────────────────────────────
async def ask_llm(prompt: str, max_retries: int = 4):
    """
    LLM call with auto-retry (4 attempts).
    Backoff: 1s → 2s → 4s → 8s between retries.
    Streams tokens normally on success.
    ✅ Continues from partial response on connection errors (no duplication).
    """
    base_url = os.getenv("LLM_BASE_URL", "https://gemma4.limeox.org/v1/chat/completions")
    model    = os.getenv("LLM_MODEL", "gemma4")
    
    accumulated_content = ""  # Track partial response for continuation
    is_continuation = False     # Flag to know if we're continuing

    headers = {"Content-Type": "application/json", "User-Agent": "ResearchBot/1.0"}

    last_error = None

    for attempt in range(1, max_retries + 1):
        logger.info(f"🤖 LLM attempt {attempt}/{max_retries} → {base_url}")
        token_count = 0
        got_any_token = False

        # Build prompt - use continuation prompt if we have partial content
        if is_continuation and accumulated_content:
            current_prompt = (
                f"Continue from EXACTLY where you left off. "
                f"Do NOT repeat anything already written. "
                f"Just continue the answer:\n\n"
                f"{accumulated_content}"
            )
        else:
            current_prompt = prompt

        json_data = {
            "model": model,
            "messages": [{"role": "user", "content": current_prompt}],
            "stream": True,
            "max_tokens": 1200,
            "temperature": 0.4,
        }

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=10.0)) as client:
                async with client.stream("POST", base_url, json=json_data, headers=headers) as resp:
                    logger.info(f"📡 LLM status: {resp.status_code}")
                    
                    # ❌ HTTP error → retry
                    if resp.status_code != 200:
                        last_error = f"HTTP {resp.status_code}"
                        logger.warning(f"⚠️ LLM attempt {attempt} failed: {last_error}")
                        if attempt < max_retries:
                            wait = 2 ** (attempt - 1)  # 1s, 2s, 4s, 8s
                            logger.info(f"⏳ Retrying in {wait}s...")
                            await asyncio.sleep(wait)
                            continue
                        else:
                            yield f"\n\n[Error: LLM failed after {max_retries} attempts (last: {last_error})]"
                            return

                    # ✅ Stream tokens
                    async for line in resp.aiter_lines():
                        if line.startswith("data: "):
                            data = line[6:]
                            if data == "[DONE]":
                                logger.info(f"✅ LLM done — {token_count} tokens (attempt {attempt})")
                                return  # ✅ success, exit retry loop
                            try:
                                chunk = json.loads(data)
                                token = chunk['choices'][0]['delta'].get('content', '')
                                if token:
                                    token_count += 1
                                    got_any_token = True
                                    accumulated_content += token  # Track for continuation
                                    yield token
                            except Exception as e:
                                logger.warning(f"⚠️ Parse error: {e}")
                                continue

                    # Stream ended without [DONE] but we got tokens → treat as success
                    if got_any_token:
                        logger.info(f"✅ LLM stream ended — {token_count} tokens (attempt {attempt})")
                        return

                    # Stream ended with NO tokens → retry
                    last_error = "Empty response"
                    logger.warning(f"⚠️ LLM attempt {attempt}: empty stream")

        except httpx.TimeoutException:
            last_error = "Timeout"
            logger.warning(f"⏱️ LLM attempt {attempt}: timeout")
        except httpx.ConnectError as e:
            last_error = f"Connection error: {e}"
            logger.warning(f"🔌 LLM attempt {attempt}: connection failed")
        except httpx.RemoteProtocolError as e:
            # ✅ Connection dropped mid-stream - try to continue
            last_error = f"RemoteProtocolError: {e}"
            logger.warning(f"🔌 LLM attempt {attempt}: connection dropped mid-stream")
            
            if got_any_token and accumulated_content:
                # Mark as continuation for next attempt
                is_continuation = True
                logger.info(f"🔄 Will continue from {len(accumulated_content)} chars...")
                # Continue to retry loop (don't return, let it retry with continuation prompt)
            else:
                logger.warning(f"❌ No partial content to continue from")
        except Exception as e:
            last_error = f"{type(e).__name__}: {e}"
            logger.warning(f"❌ LLM attempt {attempt}: {last_error}")

        # ⚠️ Non-continuation errors with partial content → can't safely retry
        if got_any_token and not isinstance(last_error, str) or "RemoteProtocolError" not in last_error:
            if got_any_token:
                logger.warning(f"⚠️ Partial response received with non-recoverable error — not retrying")
                yield f"\n\n[Connection lost. Partial response saved.]"
                return

        # Retry with backoff (for non-RemoteProtocolError OR if we have content to continue)
        if attempt < max_retries:
            wait = 2 ** (attempt - 1)  # 1s, 2s, 4s, 8s
            logger.info(f"⏳ Retrying in {wait}s...")
            await asyncio.sleep(wait)
        else:
            # Max retries with partial content
            if accumulated_content:
                yield f"\n\n[Error: Connection lost after multiple attempts. Response may be incomplete.]"
            return

    # All retries exhausted
    if accumulated_content:
        yield f"\n\n[Error: LLM failed after {max_retries} attempts. Last error: {last_error}]"
    else:
        yield f"Error: LLM failed after {max_retries} attempts. Last error: {last_error}"


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
    urls = all_urls[:10]
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