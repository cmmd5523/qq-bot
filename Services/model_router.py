"""Fixed-provider model routing. Credentials stay in private config.py."""
import json
from pathlib import Path
from openai import AsyncOpenAI

ROUTING_PATH = Path(__file__).parents[1] / 'config/model-routing.json'
PROVIDERS = {
    'deepseek': {'name': 'DeepSeek', 'model': 'deepseek-flash',
                 'base_url': 'https://api.deepseek.com', 'key': 'DEEPSEEK_API_KEY'},
    'kimi': {'name': 'Kimi', 'model': 'kimi-k2.6',
             'base_url': 'https://api.moonshot.cn/v1', 'key': 'KIMI_API_KEY'},
}
_CLIENTS = {}


def selected_provider():
    try:
        value = json.loads(ROUTING_PATH.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return 'deepseek'
    if not isinstance(value, dict) or value.get('provider') not in PROVIDERS:
        raise ValueError('Invalid model selection')
    provider = value['provider']
    if value.get('model') != PROVIDERS[provider]['model']:
        raise ValueError('Invalid provider/model pair')
    return provider


async def complete(fallback_client, *, provider=None, **kwargs):
    provider = provider or selected_provider()
    if provider not in PROVIDERS:
        raise ValueError('Unsupported model provider')
    item = PROVIDERS[provider]
    kwargs['model'] = item['model']
    if provider == 'deepseek':
        client = fallback_client
    else:
        import config
        key = getattr(config, item['key'], '')
        if not key:
            raise ValueError('Model credentials are not configured')
        if provider not in _CLIENTS:
            from Services.admin_metrics import instrument
            client = AsyncOpenAI(api_key=key, base_url=item['base_url'], timeout=25, max_retries=0)
            instrument(client)
            _CLIENTS[provider] = client
        client = _CLIENTS[provider]
        # K2.6 instant mode matches this bot's existing content-only history format.
        kwargs['temperature'] = 0.6
        kwargs['extra_body'] = {**kwargs.get('extra_body', {}), 'thinking': {'type': 'disabled'}}
        kwargs.setdefault('max_tokens', 4096)
    return await client.chat.completions.create(**kwargs)
