"""Persist aggregate AI usage, never prompts or generated text."""
from pathlib import Path
import sqlite3
import time

DB = Path(__file__).parents[1] / 'data/db/admin_metrics.db'


def record(model, response, elapsed, error=None):
    DB.parent.mkdir(parents=True, exist_ok=True)
    usage = getattr(response, 'usage', None)
    prompt = int(getattr(usage, 'prompt_tokens', 0) or 0)
    output = int(getattr(usage, 'completion_tokens', 0) or 0)
    details = getattr(usage, 'prompt_tokens_details', None)
    cached = int(getattr(usage, 'prompt_cache_hit_tokens', 0)
                 or getattr(details, 'cached_tokens', 0) or 0)
    cached = max(0, min(cached, prompt))
    from config import (DEEPSEEK_PRICE_INPUT_PER_1M_CACHE_HIT_RMB,
                        DEEPSEEK_PRICE_INPUT_PER_1M_RMB, DEEPSEEK_PRICE_OUTPUT_PER_1M_RMB)
    cost = (cached * DEEPSEEK_PRICE_INPUT_PER_1M_CACHE_HIT_RMB
            + (prompt - cached) * DEEPSEEK_PRICE_INPUT_PER_1M_RMB
            + output * DEEPSEEK_PRICE_OUTPUT_PER_1M_RMB) / 1_000_000
    with sqlite3.connect(DB, timeout=3) as db:
        db.execute('''CREATE TABLE IF NOT EXISTS requests (
            id INTEGER PRIMARY KEY, at REAL, model TEXT, prompt INTEGER,
            output INTEGER, cached INTEGER, cost REAL, elapsed REAL, error TEXT)''')
        db.execute('INSERT INTO requests(at,model,prompt,output,cached,cost,elapsed,error) VALUES(?,?,?,?,?,?,?,?)',
                   (time.time(), str(model)[:80], prompt, output, cached, cost, elapsed, error))


def instrument(client):
    endpoint = client.chat.completions
    if getattr(endpoint, '_admin_instrumented', False):
        return
    original = endpoint.create

    async def measured(*args, **kwargs):
        started = time.monotonic()
        response, error = None, None
        try:
            response = await original(*args, **kwargs)
            return response
        except Exception as exc:
            error = type(exc).__name__
            raise
        finally:
            try:
                record(kwargs.get('model', ''), response, time.monotonic() - started, error)
            except Exception:
                pass  # Metrics must never break a conversation.

    endpoint.create = measured
    endpoint._admin_instrumented = True
