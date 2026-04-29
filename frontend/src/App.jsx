import React, { useState, useCallback, useEffect, useRef, useMemo } from 'react';
import MessageBubble from './components/MessageBubble';
import useSSE from './hooks/useSSE';
import './App.css';

const App = () => {
  const [messages, setMessages] = useState([]);
  const [inputValue, setInputValue] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [sseUrl, setSseUrl] = useState(null);
  const messagesEndRef = useRef(null);
  const inputRef = useRef(null);
  const streamingIdRef = useRef(null);      // ← streaming message ka stable ID
  const doneHandledRef = useRef(false);     // ← done double-fire guard
  const sourcesRef = useRef([]);            // ← always track latest sources

  const { messages: sseMessages, done, sources, error } = useSSE(sseUrl);

  // Track latest sources in ref
  useEffect(() => {
    sourcesRef.current = sources;
  }, [sources]);

  // Auto scroll
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  // SSE tokens handle karo
  useEffect(() => {
    if (!sseUrl || sseMessages.length === 0) return;

    const streamingContent = sseMessages
      .filter(msg => msg.type === 'token')
      .map(msg => msg.text)
      .join('');

    if (!streamingContent.trim()) return;

    // ✅ KEY FIX: ID pehle set karo — setMessages ke bahar (async race condition fix)
    if (!streamingIdRef.current) {
      streamingIdRef.current = Date.now();
    }
    const currentId = streamingIdRef.current; // stable capture for closure

    setMessages(prev => {
      const newMessages = [...prev];
      const streamingIndex = newMessages.findIndex(
        msg => msg.id === currentId
      );

      if (streamingIndex !== -1) {
        // Existing message update karo
        newMessages[streamingIndex] = {
          ...newMessages[streamingIndex],
          content: streamingContent,
        };
      } else {
        // Pehli baar — ID already set hai upar se
        newMessages.push({
          id: currentId,
          role: 'assistant',
          content: streamingContent,
          streaming: true,
          sources: [],
        });
      }
      return newMessages;
    });
  }, [sseMessages, sseUrl]);

  // Done handle karo — sirf ek baar
  useEffect(() => {
    if (!done || doneHandledRef.current) return;
    doneHandledRef.current = true;

    const currentId = streamingIdRef.current;
    // Use ref to get latest sources (not stale closure value)
    const latestSources = sourcesRef.current;
    setMessages(prev =>
      prev.map(msg =>
        msg.id === currentId
          ? { ...msg, streaming: false, sources: latestSources || [] }
          : msg
      )
    );

    setSseUrl(null);
    setIsLoading(false);
    streamingIdRef.current = null;
  }, [done, sources]);

  // Get sources from last assistant message for sidebar
  const currentSources = useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i].role === 'assistant' && messages[i].sources?.length > 0) {
        return messages[i].sources;
      }
    }
    return [];
  }, [messages]);

  const getDomain = (url) => {
    try { return new URL(url).hostname.replace('www.', ''); } catch { return url; }
  };

  const handleSendMessage = useCallback((message) => {
    if (!message.trim() || isLoading) return;

    // Guards reset karo — naye query se pehle
    doneHandledRef.current = false;
    streamingIdRef.current = null;

    // User message add karo
    setMessages(prev => [...prev, {
      id: Date.now(),
      role: 'user',
      content: message.trim(),
      streaming: false,
      sources: [],
    }]);

    setInputValue('');
    setIsLoading(true);

    // SSE start karo
    const encodedQuery = encodeURIComponent(message.trim());
    setSseUrl(`/search?q=${encodedQuery}&_t=${Date.now()}`);

    setTimeout(() => inputRef.current?.focus(), 100);
  }, [isLoading]);

  const handleKeyPress = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSendMessage(inputValue);
    }
  };

  return (
    <div className="chat-app">
      <header className="chat-header">
        <div className="header-content">
          <div className="logo">
            <div className="logo-icon">M</div>
            <h1 className="title">MetaSearch AI</h1>
          </div>
          <div className="status-indicator">
            <div className={`status-dot ${isLoading ? 'loading' : 'ready'}`}></div>
            <span>{isLoading ? 'Thinking...' : 'Ready'}</span>
          </div>
        </div>
      </header>

      <main className="chat-main">
        <div className="chat-layout">
          <div className="chat-area">
            <div className="messages-container">
              {messages.length === 0 && !isLoading && (
                <div className="welcome-message">
                  <div className="welcome-icon">👋</div>
                  <h2>Welcome to MetaSearch AI</h2>
                  <p>Ask me anything! I'll search the web and provide you with comprehensive answers.</p>
                  <div className="example-queries">
                    <p>Try asking:</p>
                    <div className="examples">
                      <button onClick={() => setInputValue("What are the latest developments in AI?")}>
                        What are the latest developments in AI?
                      </button>
                      <button onClick={() => setInputValue("How does quantum computing work?")}>
                        How does quantum computing work?
                      </button>
                      <button onClick={() => setInputValue("Latest news about climate change")}>
                        Latest news about climate change
                      </button>
                    </div>
                  </div>
                </div>
              )}

              {messages.map((message) => (
                <MessageBubble key={message.id} message={message} />
              ))}

              {error && (
                <div className="error-message">
                  <div className="error-icon">⚠️</div>
                  <div className="error-content">
                    <strong>Error:</strong> {error}
                  </div>
                </div>
              )}

              <div ref={messagesEndRef} />
            </div>
          </div>

          {/* Right Sidebar - Sources Panel */}
          <div className={`sources-sidebar ${currentSources.length > 0 ? 'has-sources' : ''}`}>
            <div className="sources-panel">
              <div className="sources-panel-header">
                <span className="sources-panel-icon">🔗</span>
                <span className="sources-panel-title">Sources</span>
                <span className="sources-count">{currentSources.length}</span>
              </div>
              {currentSources.length > 0 ? (
                <div className="sources-cards">
                  {currentSources.map((source, index) => (
                    <a
                      key={index}
                      href={source}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="source-card"
                      title={source}
                    >
                      <div className="source-card-icon">
                        <span className="source-favicon">{getDomain(source)[0].toUpperCase()}</span>
                      </div>
                      <div className="source-card-content">
                        <div className="source-card-domain">{getDomain(source)}</div>
                        <div className="source-card-url">{source.length > 50 ? source.substring(0, 50) + '...' : source}</div>
                      </div>
                      <div className="source-card-number">{index + 1}</div>
                    </a>
                  ))}
                </div>
              ) : (
                <div className="sources-empty">
                  <div className="sources-empty-icon">🔍</div>
                  <p>Sources will appear here after search</p>
                </div>
              )}
            </div>
          </div>
        </div>
      </main>

      <footer className="chat-footer">
        <div className="input-container">
          <div className="input-wrapper">
            <textarea
              ref={inputRef}
              value={inputValue}
              onChange={(e) => setInputValue(e.target.value)}
              onKeyPress={handleKeyPress}
              placeholder="Ask anything..."
              className="message-input"
              rows={1}
              disabled={isLoading}
            />
            <button
              onClick={() => handleSendMessage(inputValue)}
              disabled={!inputValue.trim() || isLoading}
              className="send-button"
            >
              {isLoading ? (
                <div className="loading-spinner"></div>
              ) : (
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <line x1="22" y1="2" x2="11" y2="13"></line>
                  <polygon points="22,2 15,22 11,13 2,9 22,2"></polygon>
                </svg>
              )}
            </button>
          </div>
          <div className="input-footer">
            <span>Powered by AI • Real-time web search</span>
          </div>
        </div>
      </footer>
    </div>
  );
};

export default App;