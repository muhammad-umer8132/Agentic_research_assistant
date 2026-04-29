import React, { useMemo } from 'react';
import './MessageBubble.css';

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

    // Heading (supports # through ####)
    const headingMatch = trimmed.match(/^(#{1,4})\s+(.+)$/);
    if (headingMatch) {
      endList();
      endParagraph();
      const level = headingMatch[1].length; // 1-4
      const headingText = headingMatch[2];
      result.push(`<h${level}>${processInline(headingText)}</h${level}>`);
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

const MessageBubble = ({ message }) => {
  const { role, content, streaming } = message;

  const parsedContent = useMemo(() => {
    if (!content) return '';
    return parseMarkdown(content);
  }, [content]);

  if (role === 'user') {
    return (
      <div className="message user-message">
        <div className="message-content">
          <div className="message-text">{content}</div>
        </div>
        <div className="message-avatar">
          <div className="avatar user-avatar">U</div>
        </div>
      </div>
    );
  }

  return (
    <div className="message assistant-message">
      <div className="message-avatar">
        <div className="avatar assistant-avatar">AI</div>
      </div>
      <div className="message-content">
        <div className="message-text">
          <div 
            className="markdown-content"
            dangerouslySetInnerHTML={{ __html: parsedContent }}
          />
          {streaming && <span className="typing-cursor">|</span>}
        </div>
      </div>
    </div>
  );
};

export default MessageBubble;
