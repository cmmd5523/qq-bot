from dataclasses import dataclass
from typing import Optional

from async_lru import alru_cache
from nonebot import logger

from Services.util import global_httpx_client
from config import DEEPSEEK_API_KEY


@dataclass
class SearchResult:
    title: str
    url: str
    content: str


async def _request_tool(tool: str, body: dict) -> Optional[dict]:
    if not DEEPSEEK_API_KEY:
        logger.warning('DeepSeek search key is not configured')
        return None
    try:
        response = await global_httpx_client.post(
            f'https://api.deepseek.com/v1/tools/{tool}', json=body,
            headers={'Authorization': f'Bearer {DEEPSEEK_API_KEY}'}, timeout=30.0,
        )
        response.raise_for_status()
        return response.json()
    except Exception as error:
        logger.warning(f'DeepSeek {tool} failed: {type(error).__name__}')
        return None


@alru_cache(maxsize=256, ttl=3600)
async def _search_cached(query: str, count: int) -> Optional[list[SearchResult]]:
    data = await _request_tool('search', {'query': query, 'count': count, 'need_content': True})
    if data is None:
        return None
    return [SearchResult(str(item.get('title') or ''), str(item.get('url') or ''),
                         str(item.get('content') or item.get('summary') or item.get('snippet') or '')[:6000])
            for item in data.get('results', [])[:count]]


async def deepseek_search(query: str, max_results: int = 5) -> Optional[list[SearchResult]]:
    query = ' '.join((query or '').split())[:128]
    if not query:
        return []
    return await _search_cached(query, max(1, min(int(max_results), 10)))


@alru_cache(maxsize=128, ttl=3600)
async def deepseek_fetch(url: str) -> Optional[SearchResult]:
    data = await _request_tool('fetch', {'url': url})
    if data is None:
        return None
    return SearchResult(str(data.get('title') or ''), url, str(data.get('content') or '')[:12000])


def format_search_results_for_llm(results: list[SearchResult], max_chars: int = 12000) -> str:
    if not results:
        return '(no results)'
    return '\n\n'.join(f'[{i}] {r.title}\nURL: {r.url}\n{r.content[:2000]}'
                        for i, r in enumerate(results, 1))[:max_chars]
