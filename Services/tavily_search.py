"""Compatibility imports: search is exclusively DeepSeek Search/Fetch."""
from Services.deepseek_search import (
    SearchResult as TavilySearchResult,
    deepseek_search as tavily_search,
    format_search_results_for_llm,
)
