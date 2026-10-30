"""Telegram long polling bridge; credentials live in ignored .env."""
import asyncio
import os
from pathlib import Path
from time import monotonic

import httpx
import nonebot
from dotenv import load_dotenv
from nonebot import logger

from Services import deepseek_api
from Services.chatgpt import ChatGPTRequestMessage
from Services.telegram_access import TelegramAccess, parse_allowed_ids
from config import get_system_message

load_dotenv()
_task = None


async def _api(client, token, method, **payload):
    response = await client.post(f'https://api.telegram.org/bot{token}/{method}', json=payload)
    response.raise_for_status()
    data = response.json()
    if not data.get('ok'):
        raise RuntimeError('Telegram API rejected request')
    return data['result']


def extract_prompt(message, username, bot_id):
    text = message.get('text', '')
    private = message.get('chat', {}).get('type') == 'private'
    addressed = f'@{username}'.lower() in text.lower()
    replied = message.get('reply_to_message', {}).get('from', {}).get('id') == bot_id
    if not private and not addressed and not replied:
        return None
    import re
    text = re.sub(re.escape(f'@{username}'), '', text, flags=re.I).strip()
    if text.startswith('/start') or text.startswith('/help'):
        return ''
    for prefix in ('/chat', '/聊天助手', '聊天助手'):
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
            break
    return text if text else ''


async def _handle_message(client, token, message, me, access):
    # Authorization precedes prompt parsing and every model call, including /start.
    if not access.authorized(message):
        notice = access.denial_notice(message, monotonic())
        if notice:
            await _api(client, token, 'sendMessage', chat_id=message['chat']['id'], text=notice)
        return
    prompt = extract_prompt(message, me['username'], me['id'])
    logger.info(f'Telegram received: chat_type={message.get("chat", {}).get("type")}, addressed={prompt is not None}')
    if prompt is None:
        return
    chat_id = message['chat']['id']
    if not prompt:
        reply = '我是聊天助手。私聊直接发文字，群里 @我 或回复我的消息即可聊天。'
    else:
        result = await deepseek_api.chat(ChatGPTRequestMessage(
            message=prompt, is_chat=True, context=get_system_message(),
            group_id=f'telegram:{chat_id}:{message.get("message_thread_id", 0)}'))
        reply = str(result.message or '暂时没有得到回复，请稍后再试。')
    for start in range(0, len(reply), 2000):
        topic = {'message_thread_id': message['message_thread_id']} if message.get('message_thread_id') else {}
        await _api(client, token, 'sendMessage', chat_id=chat_id, text=reply[start:start + 2000], **topic)


async def _run(token):
    try:
        allowed = parse_allowed_ids(os.getenv('TELEGRAM_ALLOWED_USER_IDS', ''))
    except ValueError:
        logger.error('Telegram allowlist invalid; all users denied until configuration is corrected')
        allowed = frozenset()
    access = TelegramAccess(allowed)
    logger.info(f'Telegram allowlist active: {len(allowed)} authorized users; all others denied')
    state = Path('data/telegram-offset.txt')
    offset = int(state.read_text()) if state.exists() else 0
    async with httpx.AsyncClient(timeout=45, proxy=os.getenv('TELEGRAM_PROXY') or None) as client:
        me = await _api(client, token, 'getMe')
        webhook = await _api(client, token, 'getWebhookInfo')
        if webhook.get('url'):
            logger.error('Telegram webhook exists; polling not started to preserve existing integration')
            return
        logger.info(f'Telegram connected: @{me["username"]}')
        while True:
            try:
                updates = await _api(client, token, 'getUpdates', offset=offset, timeout=30, allowed_updates=['message'])
                for update in updates:
                    message = update.get('message', {})
                    if message and not message.get('from', {}).get('is_bot'):
                        await _handle_message(client, token, message, me, access)
                    offset = update['update_id'] + 1
                    state.write_text(str(offset))
            except asyncio.CancelledError:
                raise
            except Exception as error:
                status = error.response.status_code if isinstance(error, httpx.HTTPStatusError) else ''
                logger.warning(f'Telegram polling failed: {type(error).__name__} {status}')
                await asyncio.sleep(5)


@nonebot.get_driver().on_startup
async def start():
    global _task
    token = os.getenv('TELEGRAM_BOT_TOKEN', '').strip()
    if token:
        _task = asyncio.create_task(_supervise(token))
    else:
        logger.info('Telegram bridge ready; TELEGRAM_BOT_TOKEN not configured')


async def _supervise(token):
    while True:
        try:
            await _run(token)
            return
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning(f'Telegram connection failed: {type(error).__name__}; retrying in 15 seconds')
            await asyncio.sleep(15)


@nonebot.get_driver().on_shutdown
async def stop():
    if _task:
        _task.cancel()
        await asyncio.gather(_task, return_exceptions=True)
