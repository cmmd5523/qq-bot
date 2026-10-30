"""QQ voice input/output. Whisper runs locally; synthesis uses Edge online TTS."""
import asyncio
from pathlib import Path
from uuid import uuid4
import httpx
import edge_tts
from nonebot import on_message, on_command, logger
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, PrivateMessageEvent, Message, MessageSegment
from nonebot.params import CommandArg
from nonebot.matcher import Matcher
from Services import deepseek_api
from Services.chatgpt import ChatGPTRequestMessage
from config import get_system_message

_model = None
_lock = asyncio.Lock()


def _transcribe(path):
    global _model
    from faster_whisper import WhisperModel
    if _model is None:
        _model = WhisperModel('runtime/whisper-small', device='cpu', compute_type='int8')
    segments, info = _model.transcribe(str(path), language='zh', vad_filter=True, beam_size=5,
                                     temperature=0, condition_on_previous_text=False)
    if info.duration > 180:
        raise ValueError('voice too long')
    from opencc import OpenCC
    return OpenCC('t2s').convert(''.join(s.text for s in segments).strip())


async def synthesize(text, output):
    await edge_tts.Communicate(text[:1000], 'zh-CN-XiaoxiaoNeural', proxy=None).save(str(output))


async def answer(event, matcher, prompt):
    key = f'group:{event.group_id}' if isinstance(event, GroupMessageEvent) else f'private:{event.user_id}'
    result = await deepseek_api.chat(ChatGPTRequestMessage(message=prompt, is_chat=True, context=get_system_message(), group_id=key))
    text = str(result.message or '暂时没有得到回复，请稍后再试。')
    await matcher.send(text)
    output = Path('data/temp') / f'voice-{uuid4().hex}.mp3'
    try:
        await synthesize(text, output)
        await matcher.send(MessageSegment.record(output.resolve().as_uri()))
    except Exception as error:
        logger.warning(f'QQ speech output failed: {type(error).__name__}')
        await matcher.send('语音合成或发送失败，文字回复已保留。')
    finally:
        output.unlink(missing_ok=True)


async def accepts_voice(event: GroupMessageEvent | PrivateMessageEvent):
    return bool(event.get_message()['record']) and (isinstance(event, PrivateMessageEvent) or event.is_tome())


voice = on_message(rule=accepts_voice, priority=1, block=True)


@voice.handle()
async def receive(bot: Bot, event: GroupMessageEvent | PrivateMessageEvent, matcher: Matcher):
    source = Path('data/temp') / f'input-{uuid4().hex}.wav'
    try:
        async with _lock:
            record = event.get_message()['record'][0].data
            converted = await bot.get_record(file=record['file'], out_format='wav')
            value = converted.get('file', '')
            if value.startswith(('http://', 'https://')):
                async with httpx.AsyncClient(timeout=30) as client:
                    response = await client.get(value)
                    response.raise_for_status()
                    if len(response.content) > 20 * 1024 * 1024:
                        raise ValueError('voice too large')
                    source.write_bytes(response.content)
            else:
                import shutil
                if Path(value).stat().st_size > 20 * 1024 * 1024:
                    raise ValueError('voice too large')
                shutil.copyfile(value, source)
            prompt = await asyncio.to_thread(_transcribe, source)
        if not prompt:
            await matcher.send('没听清，请再发一段清晰的语音。')
            return
        await matcher.send('听到：' + prompt)
        await answer(event, matcher, prompt)
    except Exception as error:
        logger.warning(f'QQ speech input failed: {type(error).__name__}')
        await matcher.send('语音识别失败，请发三分钟内的清晰语音，或先使用文字。')
    finally:
        source.unlink(missing_ok=True)


speak = on_command('语音', block=True)


@speak.handle()
async def speak_reply(event: GroupMessageEvent | PrivateMessageEvent, matcher: Matcher, args: Message = CommandArg()):
    prompt = args.extract_plain_text().strip()
    if not prompt:
        await matcher.send('用法：/语音 你好；也可以私聊直接发送语音。')
        return
    await answer(event, matcher, prompt)
