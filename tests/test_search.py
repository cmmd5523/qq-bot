import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

ROOT = Path(__file__).parents[1]


class SearchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('test_deepseek_search_module', ROOT / 'Services/deepseek_search.py')
        self.module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = self.module
        self.post = AsyncMock()
        with patch.dict(sys.modules, {'Services.util': SimpleNamespace(global_httpx_client=SimpleNamespace(post=self.post)),
                                     'config': SimpleNamespace(DEEPSEEK_API_KEY='test-placeholder')}):
            spec.loader.exec_module(self.module)

    def response(self, status, data):
        result = SimpleNamespace(status_code=status, json=lambda: data, raise_for_status=MagicMock())
        if status >= 400:
            result.raise_for_status.side_effect = RuntimeError('request failed')
        return result

    async def test_exact_new_search_endpoint_and_body(self):
        self.post.return_value = self.response(200, {'results': [{'title': 'title', 'url': 'https://example.com', 'summary': 'summary', 'authority': 3}]})
        results = await self.module.deepseek_search('query', 5)
        self.assertEqual(results[0].content, 'summary')
        self.assertEqual(self.post.call_args.args[0], 'https://api.deepseek.com/v1/tools/search')
        self.assertEqual(self.post.call_args.kwargs['json'], {'query': 'query', 'count': 5, 'need_content': True})

    async def test_fetch_endpoint_and_private_url_rejection(self):
        self.post.return_value = self.response(200, {'title': 'test', 'content': '# content'})
        for url in ['http://127.0.0.1/a', 'http://localhost/a', 'https://user:password@example.com']:
            self.assertIsNone(await self.module.deepseek_fetch(url))
        self.post.assert_not_called()
        self.assertEqual((await self.module.deepseek_fetch('https://example.com')).content, '# content')
        self.assertEqual(self.post.call_args.args[0], 'https://api.deepseek.com/v1/tools/fetch')

    async def test_rate_limit_retries_but_auth_failure_does_not(self):
        self.post.side_effect = [self.response(429, {}), self.response(200, {'results': []})]
        with patch.object(self.module.asyncio, 'sleep', new=AsyncMock()):
            self.assertEqual(await self.module.deepseek_search('retry'), [])
        self.assertEqual(self.post.await_count, 2)
        self.post.reset_mock();self.post.side_effect = None
        self.post.return_value = self.response(401, {})
        self.assertIsNone(await self.module.deepseek_search('auth-error'))
        self.assertEqual(self.post.await_count, 1)

    async def test_failed_request_not_cached_for_an_hour(self):
        self.post.side_effect = [self.response(402, {}), self.response(200, {'results': []})]
        self.assertIsNone(await self.module.deepseek_search('retry-after-funding'))
        self.assertEqual(await self.module.deepseek_search('retry-after-funding'), [])
        self.assertEqual(self.post.await_count, 2)


if __name__ == '__main__':
    unittest.main()
