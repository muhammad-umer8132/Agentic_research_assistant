## 📊 System Architecture

### Non-Technical Overview (For Everyone)

```mermaid
flowchart LR
    Start([👤 User]) -->|Asks Question| InputBox["💬 Type Your Question"]
    
    InputBox -->|Click Search| CheckCache{{"❓ Have we<br/>answered this<br/>before?"}}
    
    CheckCache -->|Yes| UseCached["⚡ Get Answer<br/>from Memory"]
    CheckCache -->|No| MultiSearch["🔍 Search Multiple<br/>Engines<br/>Google, Bing,<br/>DuckDuckGo, Yahoo"]
    
    MultiSearch -->|Collect URLs| FetchWebsites["🌐 Fast Fetch<br/>from Websites<br/>3 Browsers<br/>Working in Parallel"]
    
    FetchWebsites -->|Extract Content| AIThinks["🧠 AI Reads Content<br/>& Writes Answer"]
    
    UseCached -->|Or| AIThinks
    
    AIThinks -->|Generate| StreamAnswer["📡 Stream Answer<br/>Word by Word"]
    
    StreamAnswer -->|Display| ChatUI["💬 Show in<br/>Chat Bubble"]
    StreamAnswer -->|Display| SourcesUI["📚 Show Sources<br/>on Right Side"]
    
    ChatUI -->|Result| End(["👤 User Sees<br/>Complete Answer<br/>with Sources"])
    SourcesUI -->|Result| End
    
    FetchWebsites -->|Save for Later| Memory["💾 Remember<br/>for Next Time"]

    style Start fill:#b3e5fc
    style InputBox fill:#81d4fa
    style CheckCache fill:#ffd54f
    style UseCached fill:#a5d6a7
    style MultiSearch fill:#ce93d8
    style FetchWebsites fill:#ffab91
    style AIThinks fill:#fff59d
    style StreamAnswer fill:#b2dfdb
    style ChatUI fill:#c5e1a5
    style SourcesUI fill:#f8bbd0
    style End fill:#a1887f
    style Memory fill:#c8e6c9
```

**User Journey Summary:**
1. **You ask** a question in the web interface
2. **System checks** if we've answered this before (instant if cached)
3. **Multiple searches** happen simultaneously across Google, Bing, DuckDuckGo, Yahoo
4. **Fast fetching** from top results using 3 parallel browsers
5. **AI processes** all content and generates comprehensive answer
6. **Stream displays** word-by-word with sources visible on the right
7. **Results cached** for future queries

---

### Technical Deep Dive (For Developers)

```mermaid
flowchart TD
    subgraph Frontend["🎨 Frontend Layer [React + Vite]"]
        UI["User Query Input"]
        SSEHook["useSSE Hook<br/>SSE Connection"]
        MessageBubble["MessageBubble Component<br/>Markdown Rendering"]
        SourcesPanel["Sources Panel<br/>Card Layout"]
    end

    subgraph FastAPIMeta["⚡ FastAPI Backend"]
        SearchEndpoint["/search Endpoint<br/>Query Handler"]
        CacheCheck["Cache Check<br/>Redis Lookup"]
        ParallelSearch["Parallel Search<br/>asyncio.gather"]
    end

    subgraph SearchEngines["🔍 Search Layer"]
        DDG["DuckDuckGo<br/>text search"]
        Google["Google Search<br/>Selenium JS Render"]
        Yahoo["Yahoo Search<br/>httpx + redirect"]
    end

    subgraph ScraperTier["📄 3-Tier Scraper"]
        Layer1["Tier 1: aiohttp<br/>Timeout: 4s<br/>Static Sites"]
        Layer2["Tier 2: curl_cffi<br/>Timeout: 8s<br/>TLS Impersonation"]
        Layer3["Tier 3: Browser Pool<br/>Timeout: 8s<br/>JS Rendering"]
    end

    subgraph BrowserPoolSubgraph["🎪 Browser Pool [3 Instances]"]
        Browser1["Browser 1<br/>SeleniumBase UC"]
        Browser2["Browser 2<br/>SeleniumBase UC"]
        Browser3["Browser 3<br/>SeleniumBase UC"]
        Semaphore["Semaphore<br/>Max: 3"]
    end

    subgraph ContentProcessing["🧠 Content Processing"]
        Trafilatura["Trafilatura<br/>Content Extraction"]
        TextClean["Text Cleanup<br/>2000 char limit"]
    end

    subgraph LLMProcessing["🤖 LLM Integration"]
        PromptBuilder["Prompt Builder<br/>Citation Format"]
        GemmaAPI["Gemma API Call<br/>httpx Streaming"]
        TokenStream["Token Stream<br/>SSE Events"]
    end

    subgraph CacheLayer["💾 Redis Cache"]
        RedisStore["Redis SETEX<br/>TTL: 3600s"]
        RedisFetch["Redis GET<br/>Key: search:query"]
    end

    UI -->|POST /search| SearchEndpoint
    SearchEndpoint -->|Check| CacheCheck
    CacheCheck -->|Cache Hit| TokenStream
    CacheCheck -->|Cache Miss| ParallelSearch
    
    ParallelSearch -->|asyncio.gather| DDG
    ParallelSearch -->|asyncio.gather| Google
    ParallelSearch -->|asyncio.gather| Yahoo
    
    DDG -->|URLs List| Layer1
    Google -->|URLs List| Layer1
    Yahoo -->|URLs List| Layer1
    
    Layer1 -->|Fail or CF| Layer2
    Layer2 -->|Fail or CF| Layer3
    
    Layer3 -->|Acquire| Semaphore
    Semaphore -->|Assign| Browser1
    Semaphore -->|Assign| Browser2
    Semaphore -->|Assign| Browser3
    
    Browser1 & Browser2 & Browser3 -->|HTML| Trafilatura
    Trafilatura -->|Content| TextClean
    TextClean -->|Dict| PromptBuilder
    
    PromptBuilder -->|Build| GemmaAPI
    GemmaAPI -->|Stream| TokenStream
    TokenStream -->|Buffer & Yield| SSEHook
    
    SSEHook -->|Display| MessageBubble
    GemmaAPI -->|Sources| SourcesPanel
    
    TokenStream -->|Cache| RedisStore
    RedisFetch -->|Cached Result| TokenStream

    style Frontend fill:#e1f5ff
    style FastAPIMeta fill:#fff3e0
    style SearchEngines fill:#f3e5f5
    style ScraperTier fill:#e8f5e9
    style BrowserPoolSubgraph fill:#fce4ec
    style ContentProcessing fill:#ede7f6
    style LLMProcessing fill:#fff9c4
    style CacheLayer fill:#c8e6c9
```

**Component Breakdown:**
- **Frontend**: React + Vite SPA with real-time SSE streaming and markdown support
- **Backend**: FastAPI async endpoints with concurrent search and scraping
- **Search Layer**: Multi-engine queries (Google/Bing/DDG/Yahoo) with JS rendering
- **Scraper Tiers**: 3-layer fallback (aiohttp → curl_cffi → BrowserPool) for reliability
- **Browser Pool**: 3 semaphore-controlled parallel browsers for speed
- **Content Processing**: Trafilatura extraction + text normalization
- **LLM Integration**: Gemma streaming API with token-by-token SSE response
- **Caching**: Redis with 1-hour TTL for performance optimization

---