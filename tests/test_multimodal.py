import importlib.util
import io
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from PIL import Image

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location('test_media_module', ROOT / 'Services/media_input.py')
media = importlib.util.module_from_spec(spec)
spec.loader.exec_module(media)


def image_bytes():
    out = io.BytesIO()
    Image.new('RGB', (32, 32), 'red').save(out, 'PNG')
    return out.getvalue()


class MediaTests(unittest.IsolatedAsyncioTestCase):
    def test_image_conversion_and_limits(self):
        self.assertTrue(media.image_part(image_bytes())['image_url']['url'].startswith('data:image/jpeg;base64,'))
        for data in [b'invalid image', b'x' * (media.IMAGE_LIMIT + 1)]:
            with self.assertRaises(ValueError):
                media.image_part(data)

    async def test_non_platform_and_private_urls_rejected(self):
        for url in ['http://127.0.0.1/a', 'https://evil.invalid/a', 'file:///etc/passwd', 'https://qq.com.evil.invalid/a']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                await media.public_download(url, 1024)

    async def test_unsupported_video_fails_before_download(self):
        router = SimpleNamespace(PROVIDERS={'deepseek': {'video': False}, 'kimi': {'video': True}})
        api = AsyncMock()
        with patch.dict(sys.modules, {'Services.model_router': router}):
            with self.assertRaises(ValueError):
                await media.telegram_parts({'video': {'file_id': 'test-video'}}, 'unused', api, 'deepseek')
        api.assert_not_called()

    async def test_telegram_photo_download_then_encoding(self):
        router = SimpleNamespace(PROVIDERS={'deepseek': {'video': False}})
        api = AsyncMock(return_value={'file_path': 'photos/test.jpg'})
        with patch.dict(sys.modules, {'Services.model_router': router}), patch.object(media, 'public_download', AsyncMock(return_value=image_bytes())):
            parts = await media.telegram_parts({'photo': [{'file_id': 'small'}, {'file_id': 'large'}]}, 'unused', api, 'deepseek')
        self.assertEqual(parts[0]['type'], 'image_url')
        api.assert_awaited_once_with('getFile', file_id='large')

    def test_real_video_probe_and_duration_limit(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'red.mp4'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=red:s=64x64:d=1', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(path)], check=True)
            self.assertTrue(media.video_part(path.read_bytes())['video_url']['url'].startswith('data:video/mp4;base64,'))
        fake = SimpleNamespace(stdout='{"format":{"duration":"121","format_name":"mov,mp4"}}')
        with patch.object(media.subprocess, 'run', return_value=fake), self.assertRaises(ValueError):
            media.video_part(b'test-video')


if __name__ == '__main__':
    unittest.main()
