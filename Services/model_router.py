"""Fixed-provider model routing. Credentials stay in private config.py."""
import json
from pathlib import Path
from openai import AsyncOpenAI

ROUTING_PATH = Path(__file__).parents[1] / 'config/model-routing.json'
PROVIDERS = {
    'deepseek': {'name': 'DeepSeek', 'model': 'deepseek-flash',
                 'base_url': 'https://api.deepseek.com', 'key': 'DEEPSEEK_API_KEY',
                 'images': True, 'video': False, 'thinking_default': True},
    'kimi': {'name': 'Kimi', 'model': 'kimi-k2.6',
             'base_url': 'https://api.moonshot.cn/v1', 'key': 'KIMI_API_KEY',
             'images': True, 'video': True, 'thinking_default': False},
}
_CLIENTS = {}


class ModelAnswer(str):
    def __new__(cls, text, reasoning='', provider=''):
        value = super().__new__(cls, text)
        value.reasoning_content = reasoning
        value.reasoning_provider = provider
        return value


def routing_settings():
    try:
        value = json.loads(ROUTING_PATH.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {'provider': 'deepseek', 'model': 'deepseek-flash'}
    if not isinstance(value, dict) or value.get('provider') not in PROVIDERS:
        raise ValueError('Invalid model selection')
    provider = value['provider']
    if value.get('model') != PROVIDERS[provider]['model']:
        raise ValueError('Invalid provider/model pair')
    modes = value.get('thinking_by_provider', {})
    if not isinstance(modes, dict) or any(k not in PROVIDERS or type(v) is not bool for k, v in modes.items()):
        raise ValueError('Invalid thinking settings')
    return value


def selected_provider():
    return routing_settings()['provider']


def thinking_enabled(provider):
    return routing_settings().get('thinking_by_provider', {}).get(provider, PROVIDERS[provider]['thinking_default'])


async def complete(fallback_client, *, provider=None, thinking=None, **kwargs):
    provider = provider or selected_provider()
    if provider not in PROVIDERS:
        raise ValueError('Unsupported model provider')
    item = PROVIDERS[provider]
    kwargs['model'] = item['model']
    thinking = thinking_enabled(provider) if thinking is None else thinking
    if type(thinking) is not bool:
        raise ValueError('Invalid thinking mode')
    kwargs.setdefault('timeout', 90 if thinking else 45)
    kwargs['extra_body'] = {**kwargs.get('extra_body', {}), 'thinking': {'type': 'enabled' if thinking else 'disabled'}}
    # Do not forward another provider's reasoning fields across model switches.
    kwargs['messages'] = [{k: v for k, v in message.items() if k != 'reasoning_content' or (provider == 'kimi' and thinking)}
                          for message in kwargs.get('messages', [])]
    if provider == 'deepseek':
        client = fallback_client
        if thinking:
            kwargs.pop('temperature', None)
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
        kwargs['temperature'] = 1.0 if thinking else 0.6
        kwargs.setdefault('max_tokens', 8192 if thinking else 4096)
    return await client.chat.completions.create(**kwargs)
