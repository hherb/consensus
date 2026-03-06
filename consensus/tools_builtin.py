"""Built-in tool providers for Consensus.

Currently provides:
- WebSearchProvider: Web search via Brave Search API (with DuckDuckGo fallback)
"""

import logging
import os
from typing import Optional

import httpx

from .tools import PythonToolProvider, ToolContext, ToolDefinition, ToolResult

logger = logging.getLogger(__name__)

# Brave Search API configuration
BRAVE_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"
BRAVE_SEARCH_TIMEOUT = 15.0

# DuckDuckGo fallback
DDG_SEARCH_URL = "https://html.duckduckgo.com/html/"
DDG_SEARCH_TIMEOUT = 10.0

WEB_SEARCH_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "The search query",
        },
        "num_results": {
            "type": "integer",
            "description": "Number of results to return (1-10, default 5)",
            "default": 5,
        },
    },
    "required": ["query"],
}


async def _brave_search(query: str, num_results: int,
                        api_key: str) -> Optional[str]:
    """Search using the Brave Search API."""
    headers = {
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
        "X-Subscription-Token": api_key,
    }
    params = {"q": query, "count": min(num_results, 10)}

    try:
        async with httpx.AsyncClient(timeout=BRAVE_SEARCH_TIMEOUT) as client:
            response = await client.get(
                BRAVE_SEARCH_URL, headers=headers, params=params,
            )
            response.raise_for_status()
            data = response.json()

        results = data.get("web", {}).get("results", [])
        if not results:
            return f'No results found for "{query}".'

        lines = [f'Search results for "{query}":\n']
        for i, r in enumerate(results[:num_results], 1):
            title = r.get("title", "No title")
            url = r.get("url", "")
            snippet = r.get("description", "No description")
            lines.append(f"{i}. {title}\n   URL: {url}\n   {snippet}\n")

        return "\n".join(lines)
    except httpx.HTTPStatusError as e:
        logger.warning("Brave Search API error: %s", e)
        return None
    except Exception as e:
        logger.warning("Brave Search failed: %s", e)
        return None


async def _ddg_search(query: str, num_results: int) -> str:
    """Fallback search using DuckDuckGo HTML endpoint."""
    try:
        async with httpx.AsyncClient(
            timeout=DDG_SEARCH_TIMEOUT,
            follow_redirects=True,
        ) as client:
            response = await client.post(
                DDG_SEARCH_URL,
                data={"q": query},
                headers={"User-Agent": "Consensus/1.0"},
            )
            response.raise_for_status()
            html = response.text

        # Simple HTML parsing for DuckDuckGo results
        results: list[dict] = []
        # DuckDuckGo results are in <a class="result__a"> tags
        import re
        # Extract result links and snippets
        result_blocks = re.findall(
            r'class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>.*?'
            r'class="result__snippet"[^>]*>(.*?)</(?:a|span|td)',
            html, re.DOTALL,
        )

        for url, title, snippet in result_blocks[:num_results]:
            # Clean HTML tags
            title = re.sub(r'<[^>]+>', '', title).strip()
            snippet = re.sub(r'<[^>]+>', '', snippet).strip()
            # DuckDuckGo wraps URLs in a redirect
            if '/l/?uddg=' in url:
                import urllib.parse
                parsed = urllib.parse.parse_qs(
                    urllib.parse.urlparse(url).query,
                )
                url = parsed.get('uddg', [url])[0]
            results.append({
                "title": title, "url": url, "snippet": snippet,
            })

        if not results:
            return f'No results found for "{query}".'

        lines = [f'Search results for "{query}":\n']
        for i, r in enumerate(results, 1):
            lines.append(
                f"{i}. {r['title']}\n   URL: {r['url']}\n   {r['snippet']}\n"
            )
        return "\n".join(lines)

    except Exception as e:
        logger.warning("DuckDuckGo search failed: %s", e)
        return f"Web search failed: {e}"


async def web_search_handler(arguments: dict,
                             context: ToolContext) -> ToolResult:
    """Execute a web search using Brave Search API or DuckDuckGo fallback."""
    query = arguments.get("query", "")
    if not query:
        return ToolResult(content="No search query provided.", is_error=True)

    num_results = min(max(arguments.get("num_results", 5), 1), 10)

    # Try Brave Search first if API key is available
    api_key = os.environ.get("BRAVE_SEARCH_API_KEY", "")
    if api_key:
        result = await _brave_search(query, num_results, api_key)
        if result:
            return ToolResult(content=result, metadata={"engine": "brave"})

    # Fall back to DuckDuckGo
    result = await _ddg_search(query, num_results)
    return ToolResult(content=result, metadata={"engine": "duckduckgo"})


def create_web_search_provider() -> PythonToolProvider:
    """Create and return the built-in web search tool provider."""
    provider = PythonToolProvider(name="builtin")
    provider.register(
        ToolDefinition(
            name="web_search",
            description=(
                "Search the web for current information. Use this when you "
                "need recent data, facts, or references that may not be in "
                "your training data."
            ),
            parameters=WEB_SEARCH_SCHEMA,
        ),
        web_search_handler,
    )
    return provider
