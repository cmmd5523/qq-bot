"""Bounded attachment ingestion. No raw media is retained in conversation history."""
import asyncio
import base64
import io
import ipaddress
import json
from pathlib import Path
import socket
import subprocess
import tempfile
from urllib.parse import urlsplit

import httpx
from PIL import Image, ImageOps, UnidentifiedImageError

IMAGE_LIMIT = 8 * 1024 * 1024
VIDEO_LIMIT = 20 * 1024 * 1024
MAX_IMAGES = 4
GATE = asyncio.Semaphore(2)
QQ_ROOTS = (Path('/app/.config/QQ'), Path('/app/napcat/cache'))


def image_part(data):
    if len(data) > IMAGE_LIMIT:
        raise ValueError('单张图片不能超过8MB。')
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format not in {'JPEG', 'PNG', 'GIF', 'WEBP'} or image.width * image.height > 20_000_000:
                raise ValueError('图片格式不支持或分辨率过大。')
            if image.format == 'GIF':
                return {'type': 'image_url', 'image_url': {'url': 'data:image/gif;base64,' + base64.b64encode(data).decode()}}
            image = ImageOps.exif_transpose(image)
            image.thumbnail((2048, 2048))
            output = io.BytesIO()
            if image.mode in {'RGBA', 'LA'} or 'transparency' in image.info:
                rgba = image.convert('RGBA')
                background = Image.new('RGBA', rgba.size, 'white')
                background.alpha_composite(rgba)
                image = background.convert('RGB')
            else:
                image = image.convert('RGB')
            image.save(output, format='JPEG', quality=88)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise ValueError('图片无法读取，请使用PNG、JPEG、WebP或GIF。') from None
    return {'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,' + base64.b64encode(output.getvalue()).decode()}}


def video_part(data):
    if len(data) > VIDEO_LIMIT:
        raise ValueError('视频不能超过20MB。')
    with tempfile.TemporaryDirectory(prefix='qqbot-media-') as folder:
        path = Path(folder) / 'video.bin'
        path.write_bytes(data)
        try:
            result = subprocess.run(['ffprobe', '-v', 'error', '-protocol_whitelist', 'file,pipe',
                '-show_entries', 'format=duration,format_name', '-of', 'json', str(path)],
                capture_output=True, text=True, timeout=10, check=True)
            info = json.loads(result.stdout)['format']
            duration = float(info['duration'])
            formats = info['format_name'].split(',')
        except (subprocess.SubprocessError, ValueError, KeyError):
            raise ValueError('视频无法读取，请使用常见MP4/WebM格式。') from None
    if not 0 < duration <= 120:
        raise ValueError('视频时长需在120秒以内。')
    choices = [('mov', 'mp4'), ('mp4', 'mp4'), ('matroska', 'webm'), ('webm', 'webm'),
               ('mpeg', 'mpeg'), ('mpegts', 'mpeg'), ('avi', 'avi'), ('flv', 'x-flv'), ('asf', 'wmv')]
    mime = next((mime for fmt, mime in choices if fmt in formats), None)
    if not mime:
        raise ValueError('不支持这种视频容器，请转为MP4。')
    return {'type': 'video_url', 'video_url': {'url': 'data:video/' + mime + ';base64,' + base64.b64encode(data).decode()}}


async def public_download(url, limit, *, telegram=False):
    parts = urlsplit(url)
    suffixes = ('qq.com', 'qq.com.cn', 'qpic.cn', 'gtimg.cn', 'qlogo.cn')
    host = (parts.hostname or '').lower()
    permitted = host == 'api.telegram.org' if telegram else any(host == suffix or host.endswith('.' + suffix) for suffix in suffixes)
    if parts.scheme not in {'http', 'https'} or parts.username or parts.password or parts.port not in {None, 80, 443} or not permitted:
        raise ValueError('附件地址不在平台允许范围内。')
    try:
        addresses = await asyncio.to_thread(socket.getaddrinfo, host, parts.port or 443)
        if any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
            raise ValueError('不允许访问内网附件地址。')
        async with GATE, httpx.AsyncClient(timeout=45, follow_redirects=False) as client:
            async with client.stream('GET', url) as response:
                response.raise_for_status()
                if int(response.headers.get('content-length', '0')) > limit:
                    raise ValueError('附件大小超过限制。')
                data = bytearray()
                async for chunk in response.aiter_bytes(65536):
                    data.extend(chunk)
                    if len(data) > limit:
                        raise ValueError('附件大小超过限制。')
                return bytes(data)
    except (httpx.HTTPError, OSError):
        # Never include signed QQ URLs or Telegram Bot tokens in a user-facing exception.
        raise ValueError('附件下载失败，请重新发送；Telegram文件需小于20MB。') from None


async def qq_parts(bot, message, provider):
    from Services.model_router import PROVIDERS
    segments = [s for s in message if s.type in {'image', 'video'}]
    if sum(s.type == 'image' for s in segments) > MAX_IMAGES or sum(s.type == 'video' for s in segments) > 1:
        raise ValueError('单条消息最多4张图片或1段视频。')
    if any(s.type == 'video' for s in segments) and not PROVIDERS[provider]['video']:
        raise ValueError('当前DeepSeek模型支持图片，不支持视频；请在后台切换Kimi。')
    results = []
    for segment in segments:
        source = str(segment.data.get('url') or segment.data.get('file') or '')
        limit = IMAGE_LIMIT if segment.type == 'image' else VIDEO_LIMIT
        if not source.startswith(('http://', 'https://')) and not source.startswith('/'):
            try:
                info = await bot.call_api('get_file', file_id=source)
                source = str(info.get('url') or info.get('file') or '')
            except Exception:
                raise ValueError('无法取得附件，请重新发送原图或视频。') from None
        if source.startswith(('http://', 'https://')):
            data = await public_download(source, limit)
        else:
            path = Path(source).resolve()
            if not any(path.is_relative_to(root) for root in QQ_ROOTS) or not path.is_file() or path.stat().st_size > limit:
                raise ValueError('附件本地路径不可用或超过大小限制。')
            data = await asyncio.to_thread(path.read_bytes)
        results.append(await asyncio.to_thread(image_part if segment.type == 'image' else video_part, data))
    return results


async def telegram_parts(message, token, api_call, provider):
    from Services.model_router import PROVIDERS
    media = []
    if message.get('photo'):
        media.append(('image', message['photo'][-1]))
    if message.get('video'):
        media.append(('video', message['video']))
    if message.get('video_note'):
        media.append(('video', message['video_note']))
    if message.get('animation'):
        item = message['animation']
        media.append(('image' if item.get('mime_type') == 'image/gif' else 'video', item))
    document = message.get('document', {})
    if document.get('mime_type', '').startswith(('image/', 'video/')):
        media.append(('video' if document['mime_type'].startswith('video/') else 'image', document))
    if any(kind == 'video' for kind, _ in media) and not PROVIDERS[provider]['video']:
        raise ValueError('当前DeepSeek模型不支持视频，请在后台切换Kimi。')
    result = []
    for kind, item in media:
        limit = IMAGE_LIMIT if kind == 'image' else VIDEO_LIMIT
        if item.get('file_size', 0) > limit:
            raise ValueError('图片需小于8MB，视频需小于20MB。')
        try:
            info = await api_call('getFile', file_id=item['file_id'])
        except Exception:
            raise ValueError('无法取得Telegram附件，请重新发送。') from None
        file = info.get('file_path', '')
        if not file or file.startswith('/') or '..' in file.split('/') or '://' in file:
            raise ValueError('Telegram附件路径无效。')
        data = await public_download('https://api.telegram.org/file/bot' + token + '/' + file, limit, telegram=True)
        result.append(await asyncio.to_thread(image_part if kind == 'image' else video_part, data))
    return result
