import React, { useState, useCallback } from 'react';
import './SearchBar.css';

/**
 * SearchBar Component
 * A sleek, rounded search input with integrated search button
 * @param {Object} props
 * @param {Function} props.onSearch - Callback function when search is submitted
 * @param {boolean} props.loading - Whether search is in progress
 */
const SearchBar = ({ onSearch, loading }) => {
  const [query, setQuery] = useState('');

  // Handle input change
  const handleInputChange = useCallback((e) => {
    setQuery(e.target.value);
  }, []);

  // Handle form submission
  const handleSubmit = useCallback((e) => {
    e.preventDefault();
    if (query.trim() && !loading) {
      onSearch(query.trim());
    }
  }, [query, onSearch, loading]);

  // Handle key press (Enter key)
  const handleKeyDown = useCallback((e) => {
    if (e.key === 'Enter' && !loading) {
      handleSubmit(e);
    }
  }, [handleSubmit, loading]);

  return (
    <div className="searchbar-container">
      <form onSubmit={handleSubmit} className="searchbar-wrapper">
        {/* Search Icon */}
        <span className="search-icon">
          <svg 
            xmlns="http://www.w3.org/2000/svg" 
            viewBox="0 0 24 24" 
            fill="none" 
            stroke="currentColor" 
            strokeWidth="2" 
            strokeLinecap="round" 
            strokeLinejoin="round"
          >
            <circle cx="11" cy="11" r="8"></circle>
            <path d="m21 21-4.35-4.35"></path>
          </svg>
        </span>

        {/* Input Field */}
        <input
          type="text"
          className="search-input"
          placeholder="Ask anything..."
          value={query}
          onChange={handleInputChange}
          onKeyDown={handleKeyDown}
          disabled={loading}
          aria-label="Search query"
        />

        {/* Search Button */}
        <button
          type="submit"
          className="search-button"
          disabled={!query.trim() || loading}
          aria-label="Search"
        >
          {loading ? (
            // Loading spinner
            <div className="spinner"></div>
          ) : (
            // Arrow icon
            <svg 
              xmlns="http://www.w3.org/2000/svg" 
              viewBox="0 0 24 24" 
              fill="none" 
              stroke="currentColor" 
              strokeWidth="2" 
              strokeLinecap="round" 
              strokeLinejoin="round"
            >
              <path d="M5 12h14"></path>
              <path d="m12 5 7 7-7 7"></path>
            </svg>
          )}
        </button>
      </form>
    </div>
  );
};

export default SearchBar;
