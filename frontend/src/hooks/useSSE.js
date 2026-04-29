import { useState, useEffect, useRef } from 'react';

/**
 * Custom hook for handling Server-Sent Events (SSE)
 * @param {string} url - The SSE endpoint URL
 * @returns {Object} { messages, done, sources, error }
 */
const useSSE = (url) => {
  const [messages, setMessages] = useState([]);
  const [done, setDone] = useState(false);
  const [sources, setSources] = useState([]);
  const [error, setError] = useState(null);

  const eventSourceRef = useRef(null);
  const finalReceivedRef = useRef(false);   // ✅ duplicate final event guard

  useEffect(() => {
    // Reset state when URL changes
    setMessages([]);
    setDone(false);
    setSources([]);
    setError(null);
    finalReceivedRef.current = false;       // ✅ reset for new query

    if (!url) return;

    const eventSource = new EventSource(url);
    eventSourceRef.current = eventSource;

    eventSource.onmessage = (event) => {
      const data = event.data;

      // End marker
      if (data === '[DONE]') {
        setDone(true);
        eventSource.close();
        return;
      }

      // Status message
      if (data.startsWith('Scraping done')) {
        setMessages(prev => [...prev, { type: 'status', text: data }]);
        return;
      }

      // Final JSON event – only set sources, do NOT add summary again
      try {
        const jsonData = JSON.parse(data);
        if (jsonData.event === 'final') {
          if (finalReceivedRef.current) return;  // ✅ ignore duplicate
          finalReceivedRef.current = true;
          setSources(jsonData.sources || []);
          setDone(true);
          eventSource.close();
          return;
        }
      } catch {
        // not JSON, treat as token
      }

      // Regular token (line) – accumulate with newline
      setMessages(prev => {
        const last = prev[prev.length - 1];
        if (last && last.type === 'token') {
          const updated = [...prev];
          updated[prev.length - 1] = {
            type: 'token',
            text: last.text + data + '\n',
          };
          return updated;
        }
        return [...prev, { type: 'token', text: data + '\n' }];
      });
    };

    eventSource.onerror = (err) => {
      console.error('SSE error:', err);
      setError('Connection error. Please try again.');
      setDone(true);
      eventSource.close();
    };

    // Cleanup on unmount or URL change
    return () => {
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
      }
    };
  }, [url]);

  return { messages, done, sources, error };
};

export default useSSE;