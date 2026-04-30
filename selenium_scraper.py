# selenium_scraper.py (updated proxies)

import json

import logging

import os

import random

import re

import socket

import time

import threading

from typing import Any, Optional



logger = logging.getLogger(__name__)



# ── curl_cffi (Layer 1 — fastest) ────────────────────────────────────────────

try:

    import curl_cffi.requests as cffi_requests

    HAS_CURL = True

except ImportError:

    HAS_CURL = False

    logger.warning("curl_cffi missing  →  pip install curl-cffi")



# ── SeleniumBase (Layer 2 — most powerful) ────────────────────────────────────

try:

    from seleniumbase import SB

    HAS_SB = True

except ImportError:

    HAS_SB = False

    logger.warning("seleniumbase missing  →  pip install seleniumbase")





# ══════════════════════════════════════════════════════════════════════════════

# CONFIGURATION

# ══════════════════════════════════════════════════════════════════════════════



class ScraperConfig:

    # ── Default proxies (hardcoded) ──

    DEFAULT_PROXIES = []



    # Build final proxy list: env var (if set) + defaults

    _env_proxies = os.getenv("SCRAPER_PROXIES", "")

    if _env_proxies:

        env_list = [p.strip() for p in _env_proxies.split(",") if p.strip()]

        PROXIES = env_list + DEFAULT_PROXIES

    else:

        PROXIES = DEFAULT_PROXIES



    # Browser

    HEADLESS: bool       = True

    WINDOW_SIZE: str     = "1920,1080"

    RECONNECT_TIME: int  = 2



    # Timeouts

    CURL_TIMEOUT: int    = 15

    PAGE_TIMEOUT: int    = 4

    CF_WAIT: float       = 1.5



    # Retry

    MAX_RETRIES: int     = 1

    RETRY_DELAY: float   = 0.5



    # Cloudflare signatures

    CF_SIGNATURES: list[str] = [

        "Just a moment",

        "cf-browser-verification",

        "challenge-platform",

        "cdn-cgi/challenge-platform",

        "Checking your browser",

        "Enable JavaScript and cookies",

        "cf_clearance",

        "__cf_chl",

        "cf-turnstile",

    ]



    # curl_cffi impersonations

    CURL_IMPERSONATIONS: list[str] = [

        "chrome124", "chrome123", "chrome110",

        "safari17_0", "firefox121",

    ]



    COMMON_HEADERS: dict = {

        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",

        "Accept-Language": "en-US,en;q=0.9",

        "Accept-Encoding": "gzip, deflate, br",

        "Connection": "keep-alive",

        "Upgrade-Insecure-Requests": "1",

        "Sec-Fetch-Dest": "document",

        "Sec-Fetch-Mode": "navigate",

        "Sec-Fetch-Site": "none",

        "Sec-Fetch-User": "?1",

    }



cfg = ScraperConfig()





# ══════════════════════════════════════════════════════════════════════════════

# HELPERS

# ══════════════════════════════════════════════════════════════════════════════



def is_internet_available() -> bool:

    try:

        socket.setdefaulttimeout(5)

        socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect(("8.8.8.8", 53))

        return True

    except Exception:

        return False



def is_cf_blocked(html: str) -> bool:

    return any(sig in html for sig in cfg.CF_SIGNATURES)



def get_proxy() -> Optional[str]:

    return random.choice(cfg.PROXIES) if cfg.PROXIES else None



def _wait_cf_clear_sync(sb, timeout_s: float = None) -> bool:

    timeout_s = timeout_s or cfg.CF_WAIT

    deadline = time.monotonic() + timeout_s

    while time.monotonic() < deadline:

        try:

            html = sb.get_page_source()

            if not is_cf_blocked(html):

                return True

        except:

            pass

        time.sleep(1.2)

    return False





# ══════════════════════════════════════════════════════════════════════════════

# LAYER 1 — curl_cffi

# ══════════════════════════════════════════════════════════════════════════════



def fetch_curl(url: str) -> Optional[str]:

    if not HAS_CURL:

        return None

    proxy = get_proxy()

    impersonation = random.choice(cfg.CURL_IMPERSONATIONS)

    proxies = {"http": proxy, "https": proxy} if proxy else None

    try:

        t0 = time.monotonic()

        resp = cffi_requests.get(

            url,

            impersonate=impersonation,

            headers=cfg.COMMON_HEADERS,

            proxies=proxies,

            timeout=cfg.CURL_TIMEOUT,

            allow_redirects=True,

        )

        if resp.status_code in {403, 429, 503}:

            return None

        html = resp.text

        if is_cf_blocked(html):

            return None

        logger.info(f"✅ [curl_cffi/{impersonation}] {url} ({time.monotonic()-t0:.2f}s)")

        return html

    except Exception as e:

        logger.debug(f"curl_cffi error: {e}")

        return None





# ══════════════════════════════════════════════════════════════════════════════

# LAYER 2 — SeleniumBase UC

# ══════════════════════════════════════════════════════════════════════════════



class BrowserSession:

    def __init__(self):

        self.sb         = None

        self._ctx       = None

        self._base_url  = None

        self._lock      = threading.Lock()



    def start(self, seed_url: str) -> bool:

        if not HAS_SB:

            return False

        proxy = get_proxy()

        logger.info(f"🚀 Browser starting (headless={cfg.HEADLESS}, proxy={proxy or 'none'})...")

        try:

            kwargs = dict(

                uc=True,

                headless=cfg.HEADLESS,

                browser="chrome",

                window_size=cfg.WINDOW_SIZE,

                incognito=True,

            )

            if proxy:

                kwargs["proxy"] = proxy

            self._ctx = SB(**kwargs)

            self.sb   = self._ctx.__enter__()

            self.sb.uc_open_with_reconnect(seed_url, reconnect_time=cfg.RECONNECT_TIME)

            time.sleep(1)

            page_src = self.sb.get_page_source().lower()

            if any(x in page_src for x in ["just a moment", "cloudflare", "turnstile"]):

                logger.info("🔒 CF challenge detected → auto-solving...")

                try:

                    self.sb.uc_gui_click_captcha()

                    time.sleep(8)

                except:

                    time.sleep(15)

            self._base_url = seed_url

            logger.info("✅ Browser session ready!")

            return True

        except Exception as e:

            logger.error(f"❌ Browser start failed: {e}")

            self.close()

            return False



    def close(self):

        if self._ctx:

            try:

                self._ctx.__exit__(None, None, None)

            except:

                pass

        self.sb   = None

        self._ctx = None

        logger.info("🛑 Browser closed")



    def recover(self, seed_url: str = None):

        target = seed_url or self._base_url

        if not target or not self.sb:

            return

        logger.info("🔄 Recovering browser session...")

        try:

            self.sb.driver.delete_all_cookies()

            self.sb.execute_script("window.localStorage.clear();")

            self.sb.execute_script("window.sessionStorage.clear();")

            self.sb.uc_open_with_reconnect(target, reconnect_time=cfg.RECONNECT_TIME)

            time.sleep(random.uniform(5, 8))

            self.sb.execute_script("window.scrollBy(0, 600);")

            time.sleep(2)

        except Exception as e:

            logger.warning(f"⚠️ Recovery error: {e}")



    def fetch_page(self, url: str) -> Optional[str]:

        if not self.sb:

            return None

        with self._lock:

            try:

                t0 = time.monotonic()

                # ⚡ udher hi timeout ko override karne ke liye ek thread timer nahi,

                # uc_open_with_reconnect ko reconnect_time=3 rakho

                self.sb.uc_open_with_reconnect(url, reconnect_time=3)



                # CF clear sirf 2 sec wait karo

                if not _wait_cf_clear_sync(self.sb, timeout_s=2):

                    logger.warning("CF still active, skipping")

                    return None



                html = self.sb.get_page_source()

                elapsed = time.monotonic() - t0

                if elapsed > 5:   

                    logger.warning(f"Skipping {url} (took {elapsed:.1f}s)")

                    return None

                if is_cf_blocked(html):

                    return None

                return html

            except Exception:

                return None



    def fetch_page_fast(self, url: str, timeout_s: float = 8.0) -> Optional[str]:

        if not self.sb:

            return None

        with self._lock:

            try:

                t0 = time.monotonic()

                driver = self.sb.driver

                

                # Hard page load timeout

                try:

                    driver.set_page_load_timeout(timeout_s)

                except:

                    pass

                

                # Direct GET — fast, no UC overhead

                try:

                    driver.get(url)

                except Exception as e:

                    # Timeout — but partial HTML might exist

                    logger.debug(f"driver.get timeout: {url[:60]}")

                    try:

                        driver.execute_script("window.stop();")

                    except:

                        pass

                

                elapsed = time.monotonic() - t0

                if elapsed > timeout_s + 3:

                    return None

                

                # Try to get HTML — even partial

                try:

                    html = driver.page_source

                except:

                    return None

                

                if not html or len(html) < 500:

                    return None

                

                # CF check — agar CF detected, thodi der wait karo

                if is_cf_blocked(html):

                    cf_deadline = time.monotonic() + 2

                    while time.monotonic() < cf_deadline:

                        time.sleep(0.5)

                        try:

                            html = driver.page_source

                            if not is_cf_blocked(html):

                                break

                        except:

                            pass

                    else:

                        # CF still blocking

                        return None

                

                return html if html and len(html) > 500 else None

                

            except Exception as e:

                logger.debug(f"fetch_page_fast error: {url[:60]} - {e}")

                return None



    def get_page_html(self, url: str, wait_seconds: float = 2.0) -> Optional[str]:

        if not self.sb:

            return None

        with self._lock:

            try:

                self.sb.uc_open_with_reconnect(url, reconnect_time=2)

                time.sleep(wait_seconds)

                return self.sb.get_page_source()

            except Exception:

                return None



    def fetch_api(self, url: str, params: dict = None, method: str = "GET",

                  body: Any = None, extra_headers: dict = None) -> tuple[Optional[int], Any]:

        if not self.sb:

            return None, None

        full_url = url

        if params:

            param_str = "&".join(f"{k}={v}" for k, v in params.items())

            full_url = f"{url}?{param_str}"

        headers = {"Accept": "application/json"}

        if extra_headers:

            headers.update(extra_headers)



        if method.upper() == "GET":

            script = """

            const resp = await fetch(arguments[0], {

                method: "GET",

                headers: arguments[1],

                credentials: "include"

            });

            let data; const ct = resp.headers.get("content-type") || "";

            data = ct.includes("application/json") ? await resp.json() : await resp.text();

            return { status: resp.status, data: data };

            """

            args = [full_url, headers]

        else:

            script = """

            const resp = await fetch(arguments[0], {

                method: arguments[1],

                headers: arguments[2],

                credentials: "include",

                body: arguments[3] ? JSON.stringify(arguments[3]) : null

            });

            let data; const ct = resp.headers.get("content-type") || "";

            data = ct.includes("application/json") ? await resp.json() : await resp.text();

            return { status: resp.status, data: data };

            """

            args = [full_url, method.upper(), headers, body]



        with self._lock:

            try:

                t0 = time.monotonic()

                result = self.sb.execute_script(script, *args)

                status = result.get("status")

                data = result.get("data")

                logger.info(f"✅ [seleniumbase/api] {full_url} → HTTP {status} ({time.monotonic()-t0:.2f}s)")

                return status, data

            except Exception as e:

                logger.error(f"❌ Browser API fetch error: {e}")

                raise





# ══════════════════════════════════════════════════════════════════════════════

# UNIVERSAL SCRAPER

# ══════════════════════════════════════════════════════════════════════════════



class UniversalScraper:

    def __init__(self):

        self._session = BrowserSession()

        self._ready   = False



    def get_page_html(self, url: str, wait_seconds: float = 2.0) -> Optional[str]:

        """Google/Bing search results ke liye — sirf JS render, no retries."""

        if not self._ready:

            return None

        return self._session.get_page_html(url, wait_seconds)



    def init(self, seed_url: str) -> bool:

        self._ready = self._session.start(seed_url)

        return self._ready



    def close(self):

        self._session.close()

        self._ready = False



    def fetch_html(self, url: str, use_curl_first: bool = True) -> Optional[str]:

        if not is_internet_available():

            return None



        # Layer 1: curl_cffi

        if use_curl_first and HAS_CURL:

            for attempt in range(cfg.MAX_RETRIES):

                result = fetch_curl(url)

                if result:

                    return result

                if attempt < cfg.MAX_RETRIES - 1:

                    time.sleep(cfg.RETRY_DELAY)



        # Layer 2: SeleniumBase

        if not self._ready:

            logger.error("Browser not initialized")

            return None



        for attempt in range(cfg.MAX_RETRIES):

            try:

                result = self._session.fetch_page(url)

                if result:

                    return result

            except Exception as e:

                logger.warning(f"SB fetch attempt {attempt+1}: {e}")

                if attempt == 0:

                    self._session.recover(url)

            if attempt < cfg.MAX_RETRIES - 1:

                time.sleep(cfg.RETRY_DELAY * (attempt + 1))

        return None



    def fetch_api(self, url: str, params: dict = None, method: str = "GET",

                  body: Any = None, extra_headers: dict = None,

                  use_curl_first: bool = True) -> tuple[Optional[int], Any]:

        if not is_internet_available():

            return None, None



        # Layer 1: curl_cffi (GET only)

        if use_curl_first and method.upper() == "GET" and HAS_CURL:

            full_url = url

            if params:

                full_url += "?" + "&".join(f"{k}={v}" for k, v in params.items())

            for attempt in range(cfg.MAX_RETRIES):

                try:

                    proxy = get_proxy()

                    proxies = {"http": proxy, "https": proxy} if proxy else None

                    impersonate = random.choice(cfg.CURL_IMPERSONATIONS)

                    h = {**cfg.COMMON_HEADERS, "Accept": "application/json"}

                    if extra_headers:

                        h.update(extra_headers)

                    resp = cffi_requests.get(

                        full_url, impersonate=impersonate, headers=h,

                        proxies=proxies, timeout=cfg.CURL_TIMEOUT, allow_redirects=True,

                    )

                    if resp.status_code == 200:

                        try:

                            data = resp.json()

                        except:

                            data = resp.text

                        if not is_cf_blocked(str(data)):

                            return 200, data

                except:

                    pass

                if attempt < cfg.MAX_RETRIES - 1:

                    time.sleep(cfg.RETRY_DELAY)



        # Layer 2: Browser

        if not self._ready:

            return None, None



        for attempt in range(cfg.MAX_RETRIES):

            try:

                status, data = self._session.fetch_api(

                    url, params=params, method=method,

                    body=body, extra_headers=extra_headers

                )

                if status is not None:

                    return status, data

            except:

                if attempt == 0:

                    self._session.recover()

            if attempt < cfg.MAX_RETRIES - 1:

                time.sleep(cfg.RETRY_DELAY * (attempt + 1))

        return None, None



# ══════════════════════════════════════════════════════════════════════════════

# BROWSER POOL — multiple browsers for parallel scraping

# ══════════════════════════════════════════════════════════════════════════════



class BrowserPool:

    """

    Multiple browser instances rakhta hai pool mein.

    Har scraper alag browser use karta hai → true parallelism.

    """

    def __init__(self, size: int = 3):

        self.size = size

        self._pool: list[BrowserSession] = []

        self._available: list[BrowserSession] = []

        self._lock = threading.Lock()

        self._semaphore = threading.Semaphore(size)

        self._initialized = False



    def init(self, seed_url: str = "https://example.com"):

        """Pool mein N browsers PARALLEL launch karo."""

        if self._initialized:

            return

        logger.info(f"🚀 Launching browser pool (size={self.size}) in parallel...")

        

        sessions = [BrowserSession() for _ in range(self.size)]

        threads = []

        results = [False] * self.size

        

        def _start_one(idx, session):

            results[idx] = session.start(seed_url)

        

        # Saare browsers ek saath launch karo

        for i, s in enumerate(sessions):

            t = threading.Thread(target=_start_one, args=(i, s), daemon=True)

            t.start()

            threads.append(t)

        

        # Sab ke ready hone ka wait karo

        for t in threads:

            t.join()

        

        # Successful sessions ko pool mein add karo

        for i, (s, ok) in enumerate(zip(sessions, results)):

            if ok:

                self._pool.append(s)

                self._available.append(s)

                logger.info(f"✅ Browser {i+1}/{self.size} ready")

            else:

                logger.warning(f"❌ Browser {i+1} failed")

        

        self._initialized = True

        logger.info(f"✅ Pool ready: {len(self._pool)} browsers")



    def _acquire(self) -> Optional[BrowserSession]:

        """Ek free browser nikalo pool se. Block agar sab busy hain."""

        self._semaphore.acquire()

        with self._lock:

            if not self._available:

                self._semaphore.release()

                return None

            return self._available.pop()



    def _release(self, session: BrowserSession):

        """Browser wapis pool mein dal do."""

        with self._lock:

            self._available.append(session)

        self._semaphore.release()



    def fetch_html(self, url: str, timeout_s: float = 8.0) -> Optional[str]:

        """Pool se browser le ke URL fetch karo, phir wapis daal do."""

        session = self._acquire()

        if not session:

            return None

        try:

            t0 = time.monotonic()

            html = session.fetch_page_fast(url, timeout_s=timeout_s)

            elapsed = time.monotonic() - t0

            if html:

                logger.info(f"✅ [pool] {url[:60]} ({elapsed:.1f}s)")

            else:

                logger.warning(f"❌ [pool] {url[:60]} failed ({elapsed:.1f}s)")

            return html

        finally:

            self._release(session)



    def close(self):

        """Saare browsers band karo."""

        for s in self._pool:

            s.close()

        self._pool.clear()

        self._available.clear()

        self._initialized = False

        logger.info("🛑 Pool closed")