import asyncio
import aiohttp
import logging
import threading
from trafilatura import extract
from selenium_scraper import UniversalScraper, BrowserPool, cfg, HAS_CURL

logger = logging.getLogger(__name__)

_selenium_scraper = None      
_browser_pool = None          
_search_scraper = None 

def init_selenium():
    
    global _selenium_scraper, _browser_pool, _search_scraper
    
    def _init_search():
        global _selenium_scraper, _search_scraper
        if _selenium_scraper is None:
            _selenium_scraper = UniversalScraper()
            _selenium_scraper.init("https://www.google.com")
            _search_scraper = _selenium_scraper 
    
    def _init_pool():
        global _browser_pool
        if _browser_pool is None:
            _browser_pool = BrowserPool(size=3)
            _browser_pool.init("https://example.com")
    
    # Dono parallel chalao
    t1 = threading.Thread(target=_init_search, daemon=True)
    t2 = threading.Thread(target=_init_pool, daemon=True)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    
    logger.info("✅ All browsers initialized")

def close_selenium():
    global _selenium_scraper, _browser_pool
    if _selenium_scraper:
        _selenium_scraper.close()
        _selenium_scraper = None
        _search_scraper = None
    if _browser_pool:
        _browser_pool.close()
        _browser_pool = None

def extract_clean_text(html):
    if not html:
        return None
    text = extract(html, output_format='txt', no_fallback=True)
    return text.strip()[:2000] if text else None

async def fetch_static_html(session, url):
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        async with session.get(url, timeout=4, headers=headers) as resp:
            if resp.status == 200:
                return await resp.text()
    except:
        pass
    return None

async def scrape_one(session, url, sem):
    async with sem:
        # ── Layer 1: Static aiohttp (4s) ──
        html = await fetch_static_html(session, url)
        if html and len(html) > 200 and "cf-browser-verification" not in html:
            text = extract_clean_text(html)
            if text:
                return url, text

        # ── Layer 2: curl_cffi (8s) ──
        if HAS_CURL:
            try:
                loop = asyncio.get_event_loop()
                import curl_cffi.requests as cffi_requests
                def _curl_fetch():
                    try:
                        resp = cffi_requests.get(
                            url,
                            impersonate="chrome124",
                            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
                            timeout=8,
                            allow_redirects=True,
                        )
                        if resp.status_code == 200:
                            return resp.text
                    except:
                        pass
                    return None
                html = await loop.run_in_executor(None, _curl_fetch)
                if html and "cf-browser-verification" not in html:
                    text = extract_clean_text(html)
                    if text:
                        return url, text
            except Exception:
                pass

        # ── Layer 3: Browser Pool (8s max) — TRUE PARALLEL ──
        try:
            loop = asyncio.get_event_loop()
            html = await loop.run_in_executor(
                None, lambda: _browser_pool.fetch_html(url, timeout_s=8.0)
            )
            if html:
                text = extract_clean_text(html)
                if text:
                    return url, text
        except Exception as e:
            logger.error(f"Pool fetch failed for {url}: {e}")

        return url, None

async def scrape_urls(urls, concurrency=10):

    sem = asyncio.Semaphore(concurrency)
    
    async with aiohttp.ClientSession() as session:
        tasks = [scrape_one(session, url, sem) for url in urls]
        results = await asyncio.gather(*tasks)
    
    return {url: text for url, text in results if text}