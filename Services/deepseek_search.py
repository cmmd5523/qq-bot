from dataclasses import dataclass
from typing import Optional
import asyncio
import ipaddress
import httpx
from urllib.parse import urlsplit

from async_lru import alru_cache
from nonebot import logger

from Services.util import global_httpx_client
from config import DEEPSEEK_API_KEY


@dataclass
class SearchResult:
    title: str
    url: str
    content: str
    date: str = ''
    authority: int = -1
    rerank_score: float = 0


class ToolUnavailable(Exception):
    pass


async def _request_tool(tool: str, body: dict) -> Optional[dict]:
    if tool not in {'search', 'fetch'}:
        raise ValueError('Unsupported search tool')
    if not DEEPSEEK_API_KEY:
        logger.warning('DeepSeek search key is not configured')
        return None
    for attempt in range(2):
        try:
            response = await global_httpx_client.post(
                f'https://api.deepseek.com/v1/tools/{tool}', json=body,
                headers={'Authorization': f'Bearer {DEEPSEEK_API_KEY}'}, timeout=30.0,
            )
            if response.status_code in {408, 429, 500, 502, 503, 504} and not attempt:
                await asyncio.sleep(.5)
                continue
            response.raise_for_status()
            return response.json()
        except Exception as error:
            if not attempt and isinstance(error, (httpx.TimeoutException, httpx.TransportError)):
                await asyncio.sleep(.5)
                continue
            logger.warning(f'DeepSeek {tool} failed: {type(error).__name__}')
            return None


@alru_cache(maxsize=256, ttl=3600)
async def _search_cached(query: str, count: int) -> Optional[list[SearchResult]]:
    data = await _request_tool('search', {'query': query, 'count': count, 'need_content': True})
    if data is None:
        raise ToolUnavailable()
    return [SearchResult(str(item.get('title') or ''), str(item.get('url') or ''),
                         str(item.get('content') or item.get('summary') or item.get('snippet') or '')[:6000],
                         str(item.get('date') or ''), int(item['authority']) if item.get('authority') is not None else -1, float(item.get('rerank_score') or 0))
            for item in data.get('results', [])[:count]]


async def deepseek_search(query: str, max_results: int = 5) -> Optional[list[SearchResult]]:
    query = ' '.join((query or '').split())[:128]
    if not query:
        return []
    try:
        return await _search_cached(query, max(1, min(int(max_results), 10)))
    except ToolUnavailable:
        return None


@alru_cache(maxsize=128, ttl=3600)
async def _fetch_cached(url: str) -> Optional[SearchResult]:
    data = await _request_tool('fetch', {'url': url})
    if data is None:
        raise ToolUnavailable()
    return SearchResult(str(data.get('title') or ''), url, str(data.get('content') or '')[:12000])


async def deepseek_fetch(url: str) -> Optional[SearchResult]:
    if not isinstance(url, str) or len(url) > 8192:
        return None
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password:
            return None
        if parsed.hostname in {'localhost', 'localhost.localdomain'} or parsed.hostname.endswith(('.local', '.internal')):
            return None
        try:
            if not ipaddress.ip_address(parsed.hostname).is_global:
                return None
        except ValueError:
            pass
        return await _fetch_cached(url)
    except (ToolUnavailable, ValueError):
        return None


def format_search_results_for_llm(results: list[SearchResult], max_chars: int = 12000) -> str:
    if not results:
        return '(no results)'
    return '\n\n'.join(f'[{i}] {r.title}\nURL: {r.url}\n日期: {r.date or "未知"}；权威度: {r.authority}\n{r.content[:2000]}'
                        for i, r in enumerate(results, 1))[:max_chars]
