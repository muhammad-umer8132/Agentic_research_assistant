import React, { useMemo } from 'react';
import './SummaryCard.css';

/**
 * parseMarkdown — custom robust Markdown → HTML
 * Supports: ### headers, **bold**, *italic*, bullets (*), numbered (1.),
 * and regular paragraphs.
 */
const parseMarkdown = (text) => {
  if (!text) return '';

  const lines = text.split('\n');
  const result = [];
  let i = 0;

  const processInline = (str) => {
    // Bold first (to avoid conflict with italic)
    str = str.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    // Italic (single asterisk, not part of bold leftovers)
    str = str.replace(/(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)/g, '<em>$1</em>');
    return str;
  };

  const pushList = (listItems, ordered) => {
    if (listItems.length === 0) return;
    const tag = ordered ? 'ol' : 'ul';
    const items = listItems.map(item => {
      const clean = ordered
        ? item.replace(/^\d+\.\s*/, '')
        : item.replace(/^[\*\-\+]\s*/, '');
      return `<li>${processInline(clean)}</li>`;
    });
    result.push(`<${tag}>${items.join('')}</${tag}>`);
  };

  const flushParagraph = (lines) => {
    if (lines.length === 0) return;
    const content = lines.map(processInline).join('<br>');
    result.push(`<p>${content}</p>`);
  };

  let currentParagraph = [];
  let currentList = [];
  let listType = null; // 'ul' or 'ol'

  const endList = () => {
    if (currentList.length > 0) {
      pushList(currentList, listType === 'ol');
      currentList = [];
      listType = null;
    }
  };

  const endParagraph = () => {
    if (currentParagraph.length > 0) {
      flushParagraph(currentParagraph);
      currentParagraph = [];
    }
  };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const trimmed = line.trim();

    // Heading
    if (trimmed.match(/^###\s+/)) {
      endList();
      endParagraph();
      const headingText = trimmed.replace(/^###\s+/, '');
      result.push(`<h3>${processInline(headingText)}</h3>`);
      continue;
    }

    // Empty line -> close paragraph/list
    if (trimmed === '') {
      endList();
      endParagraph();
      continue;
    }

    // Unordered list item
    if (trimmed.match(/^[\*\-\+]\s+/)) {
      endParagraph();
      if (listType === 'ol') {
        pushList(currentList, true);
        currentList = [];
        listType = 'ul';
      } else if (listType === null) {
        listType = 'ul';
      }
      currentList.push(trimmed);
      continue;
    }

    // Ordered list item
    if (trimmed.match(/^\d+\.\s+/)) {
      endParagraph();
      if (listType === 'ul') {
        pushList(currentList, false);
        currentList = [];
        listType = 'ol';
      } else if (listType === null) {
        listType = 'ol';
      }
      currentList.push(trimmed);
      continue;
    }

    // Regular text – belongs to paragraph
    endList();           // Close any open list first
    currentParagraph.push(line);
  }

  // trailing contents
  endList();
  endParagraph();

  return result.join('');
};

/**
 * SummaryCard Component
 */
const SummaryCard = ({ messages, done, sources, error }) => {
  const summaryText = useMemo(() => {
    return messages
      .filter(msg => msg.type === 'token')
      .map(msg => msg.text)
      .join('');
  }, [messages]);

  const parsedContent = useMemo(() => {
    if (!summaryText) return '';
    return parseMarkdown(summaryText);
  }, [summaryText]);

  const isScraping = useMemo(() => {
    return messages.some(msg => msg.type === 'status') && summaryText.length === 0;
  }, [messages, summaryText]);

  const getDomain = (url) => {
    try { return new URL(url).hostname.replace('www.', ''); } catch { return url; }
  };

  if (error) {
    return (
      <div className="summary-card">
        <div className="error-message">
          <span className="error-icon">⚠️</span>
          <span>{error}</span>
        </div>
      </div>
    );
  }

  if (messages.length === 0 && !error) return null;

  return (
    <div className={`summary-card ${done ? 'done' : ''}`}>
      {isScraping && (
        <div className="status-section">
          <div className="pulse-dot"></div>
          <span className="status-text">
            {messages.find(msg => msg.type === 'status')?.text || 'Processing...'}
          </span>
        </div>
      )}

      {summaryText.length > 0 && (
        <>
          <div className="overview-header">
            <div className="ai-icon">AI</div>
            <h2 className="overview-title">AI Overview</h2>
          </div>
          <div className="summary-content">
            <div
              key={summaryText.length}
              className="markdown-content"
              dangerouslySetInnerHTML={{ __html: parsedContent }}
            />
            {!done && <span className="cursor">|</span>}
          </div>
        </>
      )}

      {sources.length > 0 && (
        <div className="sources-section">
          <h3 className="sources-title">Sources</h3>
          <div className="sources-chips">
            {sources.map((source, index) => (
              <a key={index} href={source} target="_blank" rel="noopener noreferrer" className="source-chip" title={source}>
                <span className="source-number">{index + 1}</span>
                <span className="source-domain">{getDomain(source)}</span>
              </a>
            ))}
          </div>
        </div>
      )}

      {done && summaryText.length === 0 && !error && (
        <div className="empty-state">No summary available.</div>
      )}
    </div>
  );
};

export default SummaryCard;