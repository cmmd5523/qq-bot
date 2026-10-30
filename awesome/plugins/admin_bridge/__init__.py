"""Loopback-only, token-authenticated runtime management for the LAN panel."""
import asyncio
import hmac
import json
import os
import re
import time

import nonebot
from dotenv import load_dotenv
from fastapi import Header, HTTPException, Depends
from Services import deepseek_api, chatgpt_api
from Services.admin_metrics import instrument

load_dotenv()
instrument(deepseek_api.client)


def authorize(x_admin_token: str = Header(default='')):
    expected = os.getenv('ADMIN_BRIDGE_TOKEN', '')
    if len(expected) < 32 or not hmac.compare_digest(x_admin_token, expected):
        raise HTTPException(403, 'forbidden')


app = nonebot.get_app()


@app.get('/internal/admin/status', dependencies=[Depends(authorize)])
async def status():
    from awesome.plugins import telegram_bridge, group_participation
    clients = [deepseek_api, chatgpt_api]
    groups = {}
    for client in clients:
        for group, turns in client.group_information.items():
            groups[group] = groups.get(group, 0) + len(turns)
    return {'qq': [{'id': bot.self_id} for bot in nonebot.get_bots().values()],
            'telegram_polling': bool(telegram_bridge._task and not telegram_bridge._task.done()),
            'contexts': [{'group': key, 'turns': value} for key, value in groups.items()],
            'autonomous_groups': sum(group_participation.enabled(k) for k in group_participation.SETTINGS)}


@app.get('/internal/admin/groups', dependencies=[Depends(authorize)])
async def groups():
    from awesome.plugins import group_participation as p
    bots = list(nonebot.get_bots().values())
    result = []
    if bots:
        for item in await asyncio.wait_for(bots[0].get_group_list(), 8):
            gid = str(item['group_id'])
            result.append({'id': gid, 'name': item.get('group_name', gid),
                           'members': item.get('member_count', 0), 'autonomous': p.enabled(gid)})
    return result


@app.post('/internal/admin/group', dependencies=[Depends(authorize)])
async def set_group(body: dict):
    from awesome.plugins import group_participation as p
    gid = str(body.get('id', ''))
    if not re.fullmatch(r'[1-9][0-9]{0,19}', gid) or type(body.get('enabled')) is not bool:
        raise HTTPException(400, 'invalid group settings')
    changed = dict(p.SETTINGS)
    changed[gid] = {'enabled': body['enabled']}
    p.SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = p.SETTINGS_PATH.with_suffix('.tmp')
    temporary.write_text(json.dumps(changed), encoding='utf-8')
    temporary.replace(p.SETTINGS_PATH)
    p.SETTINGS.clear()
    p.SETTINGS.update(changed)
    if gid in p.TASKS:
        p.TASKS[gid].cancel()
    p.STATES.pop(gid, None)
    return {'ok': True}


@app.post('/internal/admin/context/clear', dependencies=[Depends(authorize)])
async def clear_context(body: dict):
    from awesome.plugins import group_participation as p
    from awesome.plugins.group_based_function.group_summary_store import group_summary_store
    group = str(body.get('group', '*'))
    if group != '*' and not re.fullmatch(r'[\w:-]{1,100}', group):
        raise HTTPException(400, 'invalid context')
    for client in [deepseek_api, chatgpt_api]:
        for attr in ['group_information', '_summary_by_group']:
            mapping = getattr(client, attr, {})
            mapping.clear() if group == '*' else mapping.pop(group, None)
    summary = group_summary_store._by_group
    summary.clear() if group == '*' else summary.pop(group, None)
    # Autonomous windows are keyed by QQ group ID rather than group:<ID>.
    key = group.removeprefix('group:')
    keys = list(p.STATES) if group == '*' else [key]
    for gid in keys:
        p.STATES.pop(gid, None)
        if gid in p.TASKS:
            p.TASKS[gid].cancel()
    return {'ok': True}


@app.post('/internal/admin/models/test', dependencies=[Depends(authorize)])
async def test_model(body: dict):
    from Services.model_router import complete, PROVIDERS
    provider = body.get('provider')
    if provider not in PROVIDERS:
        raise HTTPException(400, 'unsupported provider')
    started = time.monotonic()
    try:
        result = await asyncio.wait_for(complete(deepseek_api.client, provider=provider,
            messages=[{'role': 'user', 'content': '只回复OK。'}], max_tokens=64), timeout=25)
        if not result.choices[0].message.content:
            raise ValueError('empty response')
        return {'ok': True, 'provider': provider, 'model': PROVIDERS[provider]['model'],
                'elapsed': round(time.monotonic() - started, 2)}
    except Exception as error:
        status = getattr(error, 'status_code', None)
        return {'ok': False, 'error': type(error).__name__ + (f' ({status})' if status else '')}
