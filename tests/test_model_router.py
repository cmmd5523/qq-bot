import asyncio
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    sys.modules[name] = value
    spec.loader.exec_module(value)
    return value


def client():
    create = AsyncMock(return_value=SimpleNamespace())
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


class ModelRoutingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.router = load('test_routing_module', ROOT / 'Services/model_router.py')
        self.router.ROUTING_PATH = Path(self.folder.name) / 'models.json'

    def tearDown(self):
        self.folder.cleanup()

    def test_default_and_invalid_selection(self):
        self.assertEqual(self.router.selected_provider(), 'deepseek')
        for value in [{'provider': 'unknown', 'model': 'anything'}, {'provider': 'kimi', 'model': 'wrong-model'}]:
            self.router.ROUTING_PATH.write_text(json.dumps(value))
            with self.assertRaises(ValueError):
                self.router.selected_provider()

    async def test_deepseek_keeps_existing_client(self):
        fallback = client()
        await self.router.complete(fallback, messages=[], temperature=.5, thinking=False)
        fallback.chat.completions.create.assert_awaited_once_with(model='deepseek-flash', messages=[], temperature=.5,
            extra_body={'thinking': {'type': 'disabled'}}, timeout=45)

    async def test_thinking_mode_filters_temperature_and_reasoning(self):
        fallback = client()
        await self.router.complete(fallback, messages=[{'role': 'assistant', 'content': 'answer', 'reasoning_content': 'private reasoning'}], temperature=.5)
        values = fallback.chat.completions.create.call_args.kwargs
        self.assertNotIn('temperature', values)
        self.assertNotIn('reasoning_content', values['messages'][0])
        self.assertEqual(values['extra_body']['thinking']['type'], 'enabled')

    async def test_kimi_thinking_can_be_enabled_and_preserved(self):
        kimi = client()
        self.router._CLIENTS['kimi'] = kimi
        self.router.ROUTING_PATH.write_text(json.dumps({'provider': 'kimi', 'model': 'kimi-k2.6', 'thinking_by_provider': {'kimi': True}}))
        with patch.dict(sys.modules, {'config': SimpleNamespace(KIMI_API_KEY='test-placeholder')}):
            await self.router.complete(client(), messages=[{'role': 'assistant', 'content': 'answer', 'reasoning_content': 'previous reasoning'}])
        values = kimi.chat.completions.create.call_args.kwargs
        self.assertEqual(values['temperature'], 1.)
        self.assertEqual(values['messages'][0]['reasoning_content'], 'previous reasoning')
        self.assertEqual(values['extra_body']['thinking']['type'], 'enabled')

    async def test_kimi_selection_uses_exact_model_and_instant_mode(self):
        kimi, fallback = client(), client()
        self.router._CLIENTS['kimi'] = kimi
        self.router.ROUTING_PATH.write_text(json.dumps({'provider': 'kimi', 'model': 'kimi-k2.6'}))
        with patch.dict(sys.modules, {'config': SimpleNamespace(KIMI_API_KEY='test-placeholder')}):
            await self.router.complete(fallback, model='deepseek-flash', messages=[], max_tokens=512, response_format={'type': 'json_object'})
        values = kimi.chat.completions.create.call_args.kwargs
        self.assertEqual(values['model'], 'kimi-k2.6')
        self.assertEqual(values['extra_body']['thinking'], {'type': 'disabled'})
        self.assertEqual(values['temperature'], .6)
        self.assertEqual(values['max_tokens'], 512)
        fallback.chat.completions.create.assert_not_called()

    async def test_missing_key_and_unknown_provider_fail_closed(self):
        fallback = client()
        with patch.dict(sys.modules, {'config': SimpleNamespace()}):
            with self.assertRaises(ValueError):
                await self.router.complete(fallback, provider='kimi', messages=[])
        with self.assertRaises(ValueError):
            await self.router.complete(fallback, provider='https://evil.invalid', messages=[])
        fallback.chat.completions.create.assert_not_called()

    def test_kimi_cost_and_historical_backfill(self):
        metrics = load('test_routing_metrics', ROOT / 'Services/admin_metrics.py')
        metrics.DB = Path(self.folder.name) / 'metrics.db'
        usage = SimpleNamespace(prompt_tokens=100, completion_tokens=50,
                                prompt_tokens_details=SimpleNamespace(cached_tokens=20))
        config = SimpleNamespace(DEEPSEEK_PRICE_INPUT_PER_1M_CACHE_HIT_RMB=.2,
                                 DEEPSEEK_PRICE_INPUT_PER_1M_RMB=2., DEEPSEEK_PRICE_OUTPUT_PER_1M_RMB=3.)
        with patch.dict(sys.modules, {'config': config}):
            metrics.record('kimi-k2.6', SimpleNamespace(usage=usage), 1.5)
            metrics.record('deepseek-flash', SimpleNamespace(usage=usage), 1.0)
        with sqlite3.connect(metrics.DB) as db:
            rows = db.execute('SELECT provider,prompt,output,cached,cost,pricing_known FROM requests ORDER BY id').fetchall()
        self.assertEqual(rows[0][:4], ('kimi', 100, 50, 20))
        self.assertAlmostEqual(rows[0][4], .001892)
        self.assertEqual(rows[0][5], 1)
        with sqlite3.connect(metrics.DB) as db:
            db.execute("UPDATE requests SET cost=NULL,pricing_known=0 WHERE provider='kimi'")
        self.assertEqual(metrics.backfill_kimi(), 1)
        self.assertEqual(metrics.backfill_kimi(), 0)
        with sqlite3.connect(metrics.DB) as db:
            self.assertAlmostEqual(db.execute("SELECT cost FROM requests WHERE provider='kimi'").fetchone()[0], .001892)
        self.assertEqual(rows[1][:4], ('deepseek', 100, 50, 20))
        self.assertAlmostEqual(rows[1][4], .000314)


if __name__ == '__main__':
    unittest.main()
