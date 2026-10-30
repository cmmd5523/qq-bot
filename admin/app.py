"""Private LAN administration UI. Run as an unprivileged service account."""
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import subprocess
import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

HERE = Path(__file__).resolve().parent
DATA = Path(os.getenv('QQBOT_ADMIN_DATA', '/var/lib/qqbot-admin'))
DB = DATA / 'admin.sqlite3'
COOKIE = 'qqbot_admin_session'
TTL = 8 * 3600
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.mount('/static', StaticFiles(directory=HERE / 'static'), name='static')


def connect():
    DATA.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB, timeout=5)
    connection.row_factory = sqlite3.Row
    return connection


def initialize():
    with connect() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT UNIQUE,
          password TEXT NOT NULL, role TEXT NOT NULL, disabled INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY,user_id INTEGER,
          csrf TEXT,created REAL,expires REAL);
        CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,at REAL,actor TEXT,
          action TEXT,result TEXT);
        CREATE TABLE IF NOT EXISTS login_failures(ip TEXT, at REAL);
        ''')
    DB.chmod(0o600)


def password_hash(value, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', value.encode(), bytes.fromhex(salt), 600000).hex()
    return salt + ':' + digest


def check_password(value, stored):
    try:
        return hmac.compare_digest(password_hash(value, stored.split(':')[0]), stored)
    except (ValueError, TypeError):
        return False


def password_valid(value):
    return isinstance(value, str) and 12 <= len(value) <= 200


def audit(actor, action, result='ok'):
    with connect() as db:
        db.execute('INSERT INTO audit(at,actor,action,result) VALUES(?,?,?,?)',
                   (time.time(), actor, action[:100], result[:60]))


def authenticate(request, write=False, owner=False, allow_viewer=False):
    raw = request.cookies.get(COOKIE, '')
    token = hashlib.sha256(raw.encode()).hexdigest()
    with connect() as db:
        row = db.execute('''SELECT sessions.*,users.username,users.role,users.disabled FROM sessions
            JOIN users ON users.id=sessions.user_id WHERE token=? AND expires>?''', (token, time.time())).fetchone()
    if row is None or row['disabled']:
        raise HTTPException(401, '请登录管理后台')
    if write:
        origin = request.headers.get('origin')
        if origin and origin.rstrip('/') != str(request.base_url).rstrip('/'):
            raise HTTPException(403, '来源校验失败')
        if not hmac.compare_digest(request.headers.get('x-csrf-token', ''), row['csrf']):
            raise HTTPException(403, '请刷新页面后重试')
        if row['role'] == 'viewer' and not allow_viewer:
            raise HTTPException(403, '此账号仅有只读权限')
    if owner and row['role'] != 'owner':
        raise HTTPException(403, '仅后台所有者可管理账号')
    return dict(row)


def broker(action, body=None):
    result = subprocess.run(['sudo', '-n', '/usr/local/sbin/qqbot-admin-helper'],
        input=json.dumps({'action': action, 'body': body or {}}), capture_output=True, text=True, timeout=45)
    if result.returncode:
        raise HTTPException(503, '管理服务暂不可用')
    try:
        value = json.loads(result.stdout)
    except ValueError:
        raise HTTPException(503, '管理服务未返回有效结果')
    if not value.get('ok'):
        raise HTTPException(400, value.get('error', '操作失败'))
    return value['result']


@app.middleware('http')
async def secure_headers(request, call_next):
    try:
        length = int(request.headers.get('content-length', '0'))
    except ValueError:
        return JSONResponse({'detail': '无效请求长度'}, status_code=400)
    if length > 65536:
        return JSONResponse({'detail': '请求过大'}, status_code=413)
    response = await call_next(request)
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['Content-Security-Policy'] = "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    return response


@app.get('/healthz')
def health():
    return {'ok': True}


@app.get('/')
def index(request: Request):
    try:
        authenticate(request)
    except HTTPException:
        return RedirectResponse('/login', status_code=303)
    return FileResponse(HERE / 'static/index.html')


@app.get('/login')
def login_page():
    return FileResponse(HERE / 'static/login.html')


@app.post('/api/login')
def login(request: Request, body: dict):
    origin = request.headers.get('origin')
    if origin and origin.rstrip('/') != str(request.base_url).rstrip('/'):
        raise HTTPException(403, '来源校验失败')
    username, password = body.get('username', ''), body.get('password', '')
    if not isinstance(username, str) or not isinstance(password, str) or len(username) > 40 or len(password) > 200:
        raise HTTPException(400, '无效登录信息')
    ip = request.client.host
    with connect() as db:
        db.execute('DELETE FROM login_failures WHERE at<?', (time.time() - 900,))
        count = db.execute('SELECT count(*) FROM login_failures WHERE ip=?', (ip,)).fetchone()[0]
        if count >= 8:
            raise HTTPException(429, '登录尝试过多，请15分钟后重试')
        user = db.execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()
        expected = user['password'] if user else password_hash('not-a-real-password')
        if not check_password(password, expected) or not user or user['disabled']:
            db.execute('INSERT INTO login_failures VALUES(?,?)', (ip, time.time()))
            failure = True
        else:
            failure = False
            token = secrets.token_urlsafe(48)
            csrf = secrets.token_urlsafe(32)
            db.execute('DELETE FROM sessions WHERE expires<?', (time.time(),))
            db.execute('INSERT INTO sessions VALUES(?,?,?,?,?)',
                       (hashlib.sha256(token.encode()).hexdigest(), user['id'], csrf, time.time(), time.time() + TTL))
            db.execute('DELETE FROM login_failures WHERE ip=?', (ip,))
    if failure:
        audit(username, 'login', 'denied')
        raise HTTPException(401, '账号或密码错误')
    audit(username, 'login')
    response = JSONResponse({'ok': True})
    response.set_cookie(COOKIE, token, max_age=TTL, httponly=True, samesite='strict',
                        secure=os.getenv('QQBOT_ADMIN_SECURE_COOKIE', 'false') == 'true')
    return response


@app.get('/api/session')
def session(request: Request):
    user = authenticate(request)
    return {'username': user['username'], 'role': user['role'], 'csrf': user['csrf']}


@app.post('/api/logout')
def logout(request: Request):
    user = authenticate(request, write=True, allow_viewer=True)
    with connect() as db:
        db.execute('DELETE FROM sessions WHERE token=?', (user['token'],))
    response = JSONResponse({'ok': True})
    response.delete_cookie(COOKIE)
    return response


READ_ACTIONS = {'status', 'config', 'models', 'groups', 'memories', 'prompt', 'qr', 'backup_list', 'logs'}
WRITE_ACTIONS = {'service', 'telegram', 'keys', 'model_select', 'model_test', 'group', 'clear_context', 'memory_delete', 'memory_update', 'backup_create', 'backup_restore'}


@app.get('/api/data/{action}')
def read_data(action: str, request: Request, service: str = 'bot'):
    authenticate(request)
    if action not in READ_ACTIONS:
        raise HTTPException(404, '不存在')
    return broker(action, {'service': service} if action == 'logs' else {})


@app.post('/api/action/{action}')
def write_data(action: str, request: Request, body: dict):
    user = authenticate(request, write=True)
    if action == 'prompt':
        body['save'] = True
    elif action not in WRITE_ACTIONS:
        raise HTTPException(404, '不存在')
    try:
        result = broker(action, body)
    except HTTPException:
        audit(user['username'], action, 'failed')
        raise
    event = action
    if action == 'service':
        event += ':' + str(body.get('service', '')) + ':' + str(body.get('operation', ''))
    if action in {'memory_delete', 'memory_update', 'group'}:
        event += ':' + str(body.get('id', ''))
    audit(user['username'], event)
    return result


@app.get('/api/audit')
def audit_list(request: Request):
    authenticate(request)
    with connect() as db:
        return [dict(r) for r in db.execute('SELECT at,actor,action,result FROM audit ORDER BY id DESC LIMIT 100')]


@app.get('/api/users')
def users(request: Request):
    authenticate(request, owner=True)
    with connect() as db:
        return [dict(r) for r in db.execute('SELECT id,username,role,disabled FROM users ORDER BY id')]


@app.post('/api/users')
def create_user(request: Request, body: dict):
    user = authenticate(request, write=True, owner=True)
    name, password, role = body.get('username', ''), body.get('password', ''), body.get('role', 'viewer')
    if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_-]{3,32}', name) or not password_valid(password) or role not in {'admin', 'viewer'}:
        raise HTTPException(400, '用户名3–32位，密码至少12位，角色需为admin/viewer')
    try:
        with connect() as db:
            db.execute('INSERT INTO users(username,password,role) VALUES(?,?,?)', (name, password_hash(password), role))
    except sqlite3.IntegrityError:
        raise HTTPException(400, '用户名已存在')
    audit(user['username'], 'user_create:' + name)
    return {'ok': True}


@app.post('/api/users/disable')
def disable_user(request: Request, body: dict):
    user = authenticate(request, write=True, owner=True)
    target = body.get('id')
    with connect() as db:
        row = db.execute('SELECT * FROM users WHERE id=?', (target,)).fetchone()
        if not row or row['role'] == 'owner':
            raise HTTPException(400, '不能停用所有者账号')
        disabled = body.get('disabled', True)
        if type(disabled) is not bool:
            raise HTTPException(400, '无效账号状态')
        db.execute('UPDATE users SET disabled=? WHERE id=?', (int(disabled), target))
        db.execute('DELETE FROM sessions WHERE user_id=?', (target,))
    audit(user['username'], 'user_disable')
    return {'ok': True}


@app.post('/api/password')
def password_change(request: Request, body: dict):
    user = authenticate(request, write=True, allow_viewer=True)
    new, old = body.get('new', ''), body.get('old', '')
    if not password_valid(new) or not isinstance(old, str) or len(old) > 200:
        raise HTTPException(400, '新密码至少12位')
    with connect() as db:
        row = db.execute('SELECT password FROM users WHERE id=?', (user['user_id'],)).fetchone()
        if not check_password(old, row['password']):
            raise HTTPException(400, '原密码错误')
        db.execute('UPDATE users SET password=? WHERE id=?', (password_hash(new), user['user_id']))
        db.execute('DELETE FROM sessions WHERE user_id=? AND token<>?', (user['user_id'], user['token']))
    audit(user['username'], 'password_change')
    return {'ok': True}


initialize()

if __name__ == '__main__':
    import sys
    if sys.argv[1:] == ['--init']:
        value = json.load(sys.stdin)
        if not password_valid(value['password']):
            raise SystemExit('Password must be at least 12 characters')
        with connect() as db:
            db.execute('INSERT INTO users(username,password,role) VALUES(?,?,?)',
                       (value['username'], password_hash(value['password']), 'owner'))
        print('Owner initialized')
