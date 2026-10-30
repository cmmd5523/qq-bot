#!/usr/bin/python3
"""Root-owned, fixed-operation broker. No shell, arbitrary paths or Docker socket exposure."""
import ast
import base64
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile

ROOT = Path('/opt/qq-bot-deploy')
APP = ROOT / 'app'
SERVICES = {'bot': 'qq-bot-deploy_bot_1', 'napcat': 'qq-bot-deploy_napcat_1'}
KEYS = ('DEEPSEEK_API_KEY', 'OPEN_API_KEY', 'XAI_API_KEY', 'TAVILY_API_KEY',
        'PIXIV_REFRESH_TOKEN', 'BILI_SESS_DATA', 'SAUCE_API_KEY', 'DISCORD_AUTH', 'BUFF_SESSION_ID')
BACKUP_FILES = ('.env', 'config.py', 'system_prompt.md', 'config/group-participation.json')


def run(args, timeout=20):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise ValueError('服务操作失败，请检查服务状态')
    return result.stdout


def env():
    result = {}
    for line in (APP / '.env').read_text(encoding='utf-8').splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            key, value = line.split('=', 1)
            result[key.strip()] = value.strip().strip('"\'')
    return result


def atomic(path, text):
    if path.is_symlink() or not path.resolve().is_relative_to(APP.resolve()):
        raise ValueError('不允许符号链接')
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.admin-', dir=path.parent)
    temporary = Path(name)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        stream.write(text)
    temporary.replace(path)
    path.chmod(0o600)


def write_env(updates):
    text = (APP / '.env').read_text(encoding='utf-8')
    for key, value in updates.items():
        if '\n' in value or '\r' in value:
            raise ValueError('配置值不能换行')
        line = key + '=' + value
        text = re.sub(r'^' + re.escape(key) + r'=.*$', lambda _: line, text, flags=re.M) if re.search(
            r'^' + re.escape(key) + '=', text, re.M) else text.rstrip() + '\n' + line + '\n'
    atomic(APP / '.env', text)


def literals():
    values = {}
    for node in ast.walk(ast.parse((APP / 'config.py').read_text(encoding='utf-8'))):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            name = ast.unparse(node.targets[0] if isinstance(node, ast.Assign) else node.target)
            try:
                values[name] = ast.literal_eval(node.value)
            except (ValueError, TypeError):
                pass
    return values


def redact(text):
    values = list(env().values()) + [v for k, v in literals().items() if k in KEYS]
    for value in sorted((v for v in values if isinstance(v, str) and len(v) >= 8), key=len, reverse=True):
        text = text.replace(value, '[已隐藏]')
    text = re.sub(r'(?i)(token=)[^\s&]+', r'\1[已隐藏]', text)
    text = re.sub(r'\b\d{8,12}:[A-Za-z0-9_-]{25,}', '[已隐藏]', text)
    text = re.sub(r'\b(?:sk-|gh[pousr]_)[A-Za-z0-9_-]{16,}', '[已隐藏]', text)
    return re.sub(r'\x1b\[[0-9;]*m', '', text)


def bridge(path, body=None):
    token = env().get('ADMIN_BRIDGE_TOKEN', '')
    request = urllib.request.Request('http://127.0.0.1:5700/internal/admin/' + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={'X-Admin-Token': token, 'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=12) as response:
        return json.load(response)


def service(name):
    if name not in SERVICES:
        raise ValueError('未知服务')
    return SERVICES[name]


def inspect():
    results = []
    for name, container in SERVICES.items():
        try:
            item = json.loads(run(['docker', 'inspect', container]))[0]
            state = item['State']
            results.append({'name': name, 'status': state['Status'], 'started': state['StartedAt'],
                            'restarts': item['RestartCount'], 'oom': state['OOMKilled']})
        except Exception:
            results.append({'name': name, 'status': 'unknown'})
    return results


def metrics():
    path = APP / 'data/db/admin_metrics.db'
    empty = {'requests': 0, 'errors': 0, 'tokens': 0, 'cost': 0, 'avg_latency': 0, 'daily': []}
    if not path.exists():
        return empty
    with sqlite3.connect(path) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='requests'").fetchone():
            return empty
        row = db.execute('SELECT count(*),sum(error IS NOT NULL),sum(prompt+output),sum(cost),avg(elapsed) FROM requests WHERE at>?', (time.time() - 86400,)).fetchone()
        daily = db.execute("SELECT date(at,'unixepoch','+8 hours'),count(*),sum(prompt+output),sum(cost) FROM requests WHERE at>? GROUP BY 1 ORDER BY 1", (time.time() - 7 * 86400,)).fetchall()
    return dict(zip(['requests', 'errors', 'tokens', 'cost', 'avg_latency'], [v or 0 for v in row]),
                daily=[{'day': d, 'requests': r, 'tokens': t, 'cost': c} for d, r, t, c in daily])


def config_view():
    values = env()
    ids = [x for x in re.split(r'[,\s]+', values.get('TELEGRAM_ALLOWED_USER_IDS', '')) if x]
    return {'telegram_allowed': ids, 'telegram_configured': bool(values.get('TELEGRAM_BOT_TOKEN')),
            'keys': {k: bool(literals().get(k)) for k in KEYS}}


def do(action, body):
    if action == 'status':
        try:
            runtime = bridge('status')
        except Exception:
            runtime = {'unavailable': True}
        memory = {}
        for line in Path('/proc/meminfo').read_text().splitlines():
            if line.startswith(('MemTotal:', 'MemAvailable:')):
                key, value, *_ = line.split()
                memory[key.rstrip(':')] = int(value) * 1024
        disk = os.statvfs(ROOT)
        return {'services': inspect(), 'runtime': runtime, 'memory': memory,
                'disk': {'total': disk.f_blocks * disk.f_frsize, 'free': disk.f_bavail * disk.f_frsize},
                'load': os.getloadavg()[0], 'metrics': metrics(), 'time': time.time()}
    if action == 'service':
        operation = body.get('operation')
        if operation not in {'start', 'stop', 'restart'}:
            raise ValueError('无效操作')
        run(['docker', operation, service(body.get('service'))], timeout=25)
        return {'ok': True}
    if action == 'logs':
        lines = body.get('lines', 120)
        if type(lines) is not int or not 1 <= lines <= 500:
            raise ValueError('无效日志行数')
        result = subprocess.run(['docker', 'logs', '--tail', str(lines), service(body.get('service', 'bot'))],
                                capture_output=True, text=True, timeout=10)
        return {'text': redact((result.stdout + result.stderr)[-150000:])}
    if action == 'config':
        return config_view()
    if action == 'telegram':
        ids = body.get('ids')
        if not isinstance(ids, list) or len(ids) > 200 or any(
                not isinstance(x, str) or not re.fullmatch(r'[1-9][0-9]{0,18}', x) or int(x) > 2**63 - 1 for x in ids):
            raise ValueError('请填写有效的数字用户ID')
        updates = {'TELEGRAM_ALLOWED_USER_IDS': ','.join(dict.fromkeys(ids))}
        token = body.get('token')
        if token:
            if not isinstance(token, str) or not re.fullmatch(r'[0-9]{6,15}:[A-Za-z0-9_-]{20,100}', token):
                raise ValueError('无效Telegram Token格式')
            updates['TELEGRAM_BOT_TOKEN'] = token
        write_env(updates)
        run(['docker', 'restart', SERVICES['bot']])
        return {'ok': True, 'allowed_count': len(ids)}
    if action == 'keys':
        updates = body.get('values', {})
        clear = body.get('clear', [])
        if not isinstance(updates, dict) or not isinstance(clear, list) or any(k not in KEYS for k in list(updates) + clear):
            raise ValueError('未知密钥字段')
        if any(not isinstance(v, str) or len(v) > 4096 or '\n' in v or '\r' in v for v in updates.values()):
            raise ValueError('无效密钥')
        changes = {k: v for k, v in updates.items() if v}
        changes.update({k: '' for k in clear})
        path = APP / 'config.py'
        text = path.read_text(encoding='utf-8')
        lines = text.splitlines(keepends=True)
        replacements = []
        found = set()
        for node in ast.parse(text).body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                key = ast.unparse(node.targets[0] if isinstance(node, ast.Assign) else node.target)
                if key in changes:
                    found.add(key)
                    replacements.append((node.lineno - 1, node.end_lineno, key + ' = ' + repr(changes[key]) + '\n'))
        for start, end, value in sorted(replacements, reverse=True):
            lines[start:end] = [value]
        output = ''.join(lines)
        for key in changes.keys() - found:
            output += '\n' + key + ' = ' + repr(changes[key]) + '\n'
        ast.parse(output)
        atomic(path, output)
        run(['docker', 'restart', SERVICES['bot']])
        return {'ok': True}
    if action == 'prompt':
        path = APP / 'system_prompt.md'
        if body.get('save'):
            text = body.get('text')
            if not isinstance(text, str) or len(text) > 20000:
                raise ValueError('提示词过长')
            atomic(path, text)
            return {'ok': True}
        return {'text': path.read_text(encoding='utf-8') if path.exists() else ''}
    if action == 'groups':
        return bridge('groups')
    if action == 'group':
        return bridge('group', body)
    if action == 'clear_context':
        if body.get('confirm') != 'CLEAR':
            raise ValueError('需要清理确认')
        return bridge('context/clear', {'group': body.get('group', '*')})
    if action == 'memories':
        path = APP / 'data/db/meme_database.db'
        if not path.exists():
            return []
        with sqlite3.connect(path) as db:
            db.row_factory = sqlite3.Row
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='memories'").fetchone():
                return []
            return [dict(r) for r in db.execute('SELECT * FROM memories ORDER BY id DESC LIMIT 200')]
    if action == 'memory_delete':
        if body.get('confirm') != 'DELETE' or type(body.get('id')) is not int or body['id'] <= 0:
            raise ValueError('需要有效记录和删除确认')
        path = APP / 'data/db/meme_database.db'
        if not path.exists():
            return {'ok': True, 'deleted': 0}
        with sqlite3.connect(path) as db:
            db.execute('PRAGMA secure_delete=ON')
            count = db.execute('DELETE FROM memories WHERE id=?', (body['id'],)).rowcount
        return {'ok': True, 'deleted': count}
    if action == 'memory_update':
        identifier = body.get('id')
        if type(identifier) is not int or identifier <= 0:
            raise ValueError('无效记忆ID')
        allowed = {'content', 'is_approved', 'is_global'}
        updates = {k: body[k] for k in allowed if k in body}
        if not updates or any(type(v) is not bool for k, v in updates.items() if k != 'content'):
            raise ValueError('无效记忆设置')
        if 'content' in updates and (not isinstance(updates['content'], str) or not 1 <= len(updates['content']) <= 3000):
            raise ValueError('记忆内容需为1–3000字')
        path = APP / 'data/db/meme_database.db'
        if not path.exists():
            raise ValueError('记忆不存在')
        with sqlite3.connect(path) as db:
            assignments = ','.join(k + '=?' for k in updates)
            count = db.execute('UPDATE memories SET ' + assignments + ",updated_at=datetime('now') WHERE id=?",
                               [int(v) if type(v) is bool else v for v in updates.values()] + [identifier]).rowcount
        return {'ok': True, 'updated': count}
    if action == 'qr':
        path = ROOT / 'napcat/cache/qrcode.png'
        return {'image': base64.b64encode(path.read_bytes()).decode() if path.exists() else None,
                'updated': path.stat().st_mtime if path.exists() else None}
    if action == 'backup_list':
        folder = ROOT / 'admin-backups'
        return [{'id': p.name, 'bytes': p.stat().st_size, 'at': p.stat().st_mtime} for p in sorted(folder.glob('*.zip'), reverse=True)]
    if action == 'backup_create':
        folder = ROOT / 'admin-backups'
        folder.mkdir(mode=0o700, exist_ok=True)
        name = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + os.urandom(3).hex() + '.zip'
        path = folder / name
        with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
            for item in BACKUP_FILES:
                source = APP / item
                if source.exists() and not source.is_symlink():
                    archive.writestr(item, source.read_bytes())
        path.chmod(0o600)
        return {'ok': True, 'id': name}
    if action == 'backup_restore':
        name = body.get('id', '')
        if not re.fullmatch(r'[0-9]{8}T[0-9]{6}Z-[a-f0-9]{6}\.zip', name) or body.get('confirm') != 'RESTORE':
            raise ValueError('无效备份或未确认')
        path = ROOT / 'admin-backups' / name
        if path.is_symlink() or not path.is_file():
            raise ValueError('备份不存在')
        with zipfile.ZipFile(path) as archive:
            if set(archive.namelist()) - set(BACKUP_FILES) or sum(x.file_size for x in archive.infolist()) > 150000:
                raise ValueError('备份结构不合法')
            values = {name: archive.read(name).decode('utf-8') for name in archive.namelist()}
        if 'config.py' in values:
            ast.parse(values['config.py'])
        if 'config/group-participation.json' in values:
            if not isinstance(json.loads(values['config/group-participation.json']), dict):
                raise ValueError('群配置不合法')
        do('backup_create', {})
        run(['docker', 'stop', SERVICES['bot']])
        try:
            for name, value in values.items():
                atomic(APP / name, value)
        finally:
            run(['docker', 'start', SERVICES['bot']])
        return {'ok': True}
    raise ValueError('不支持的管理操作')


if __name__ == '__main__':
    try:
        if len(sys.argv) != 1:
            raise ValueError('禁止命令行参数')
        raw = sys.stdin.read(65537)
        if len(raw) > 65536:
            raise ValueError('请求过大')
        request = json.loads(raw)
        if not isinstance(request, dict) or not isinstance(request.get('body', {}), dict):
            raise ValueError('无效请求')
        print(json.dumps({'ok': True, 'result': do(request['action'], request.get('body', {}))}, ensure_ascii=False))
    except Exception as exc:
        message = str(exc) if isinstance(exc, ValueError) else '操作暂不可用，请检查服务状态'
        print(json.dumps({'ok': False, 'error': message}, ensure_ascii=False))
