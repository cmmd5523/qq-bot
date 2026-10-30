import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

ROOT = Path(__file__).parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class AdminSecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        cls.env = patch.dict(os.environ, {'QQBOT_ADMIN_DATA': cls.folder.name})
        cls.env.start()
        cls.app = load('admin_test_app', ROOT / 'admin/app.py')
        with cls.app.connect() as db:
            for name, role in [('admin', 'owner'), ('reader', 'viewer')]:
                db.execute('INSERT INTO users(username,password,role) VALUES(?,?,?)',
                           (name, cls.app.password_hash('A-long-test-password'), role))

    @classmethod
    def tearDownClass(cls):
        cls.env.stop()
        cls.folder.cleanup()

    def login(self, name='admin'):
        client = TestClient(self.app.app)
        response = client.post('/api/login', json={'username': name, 'password': 'A-long-test-password'})
        self.assertEqual(response.status_code, 200)
        csrf = client.get('/api/session').json()['csrf']
        return client, {'X-CSRF-Token': csrf}

    def test_api_requires_authentication(self):
        with TestClient(self.app.app) as client, patch.object(self.app, 'broker') as broker:
            self.assertEqual(client.get('/api/data/config').status_code, 401)
            self.assertEqual(client.post('/api/action/service', json={}).status_code, 401)
            broker.assert_not_called()

    def test_csrf_and_cross_origin_blocked(self):
        client, headers = self.login()
        with patch.object(self.app, 'broker') as broker:
            self.assertEqual(client.post('/api/action/service', json={}).status_code, 403)
            self.assertEqual(client.post('/api/action/service', json={}, headers={**headers, 'Origin': 'https://evil.invalid'}).status_code, 403)
            broker.assert_not_called()

    def test_viewer_cannot_write_or_manage_users(self):
        client, headers = self.login('reader')
        with patch.object(self.app, 'broker') as broker:
            self.assertEqual(client.post('/api/action/keys', json={}, headers=headers).status_code, 403)
            self.assertEqual(client.get('/api/users').status_code, 403)
            broker.assert_not_called()
        self.assertEqual(client.post('/api/logout', json={}, headers=headers).status_code, 200)
        self.assertEqual(client.get('/api/session').status_code, 401)

    def test_authenticated_operation_and_audit(self):
        client, headers = self.login()
        with patch.object(self.app, 'broker', return_value={'ok': True}) as broker:
            self.assertEqual(client.post('/api/action/group', json={'id': '1', 'enabled': True}, headers=headers).status_code, 200)
            broker.assert_called_once_with('group', {'id': '1', 'enabled': True})
        self.assertTrue(any(x['action'] == 'group:1' for x in client.get('/api/audit').json()))

    def test_password_change_revokes_other_sessions(self):
        client, headers = self.login()
        other, _ = self.login()
        response = client.post('/api/password', headers=headers,
                               json={'old': 'A-long-test-password', 'new': 'A-different-test-password'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(other.get('/api/session').status_code, 401)
        with self.app.connect() as db:
            db.execute('UPDATE users SET password=? WHERE username=?',
                       (self.app.password_hash('A-long-test-password'), 'admin'))

    def test_login_throttled_after_eight_failures(self):
        client = TestClient(self.app.app)
        try:
            with patch.object(self.app, 'check_password', return_value=False):
                for _ in range(8):
                    self.assertEqual(client.post('/api/login', json={'username': 'admin', 'password': 'wrong'}).status_code, 401)
                self.assertEqual(client.post('/api/login', json={'username': 'admin', 'password': 'wrong'}).status_code, 429)
        finally:
            with self.app.connect() as db:
                db.execute('DELETE FROM login_failures')


class HelperBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.helper = load('admin_test_helper', ROOT / 'admin/helper.py')
        self.helper.ROOT = Path(self.folder.name)
        self.helper.APP = self.helper.ROOT / 'app'
        self.helper.APP.mkdir()
        (self.helper.APP / '.env').write_text('TELEGRAM_BOT_TOKEN=private-test-token\n', encoding='utf-8')
        (self.helper.APP / 'config.py').write_text("DEEPSEEK_API_KEY='private-test-key'\n", encoding='utf-8')

    def tearDown(self):
        self.folder.cleanup()

    def test_fixed_services_only(self):
        for name in ['xray', 'bot; touch /tmp/bad', '../bot']:
            with self.assertRaises(ValueError):
                self.helper.service(name)

    def test_keys_never_returned_and_logs_redacted(self):
        result = self.helper.do('config', {})
        self.assertTrue(result['keys']['DEEPSEEK_API_KEY'])
        self.assertNotIn('private-test-key', json.dumps(result))
        self.assertNotIn('private-test-token', self.helper.redact('private-test-token private-test-key'))
        with self.assertRaises(ValueError):
            self.helper.do('keys', {'values': {'SUPER_USER': 'malicious'}})

    def test_secret_update_is_literal_not_code(self):
        value = "x'; __import__('os').system('bad'); '"
        with patch.object(self.helper, 'run'):
            self.helper.do('keys', {'values': {'DEEPSEEK_API_KEY': value}})
        self.assertEqual(self.helper.literals()['DEEPSEEK_API_KEY'], value)

    def test_invalid_ids_and_backup_paths_rejected(self):
        for body in [{'ids': ['*']}, {'ids': ['-1']}, {'ids': [1]}]:
            with self.assertRaises(ValueError):
                self.helper.do('telegram', body)
        with self.assertRaises(ValueError):
            self.helper.do('backup_restore', {'id': '../../passwd', 'confirm': 'RESTORE'})
        with self.assertRaises(ValueError):
            self.helper.atomic(self.helper.ROOT / 'outside', 'bad')

    def test_backup_restore_round_trip_and_rollback_copy(self):
        first = self.helper.do('backup_create', {})
        (self.helper.APP / 'config.py').write_text("DEEPSEEK_API_KEY='updated-test-key'\n", encoding='utf-8')
        with patch.object(self.helper, 'run'):
            self.helper.do('backup_restore', {'id': first['id'], 'confirm': 'RESTORE'})
        self.assertEqual(self.helper.literals()['DEEPSEEK_API_KEY'], 'private-test-key')
        self.assertEqual(len(self.helper.do('backup_list', {})), 2)

    def test_memory_edit_uses_bound_values(self):
        folder = self.helper.APP / 'data/db'
        folder.mkdir(parents=True)
        import sqlite3
        with sqlite3.connect(folder / 'meme_database.db') as db:
            db.execute('CREATE TABLE memories(id INTEGER PRIMARY KEY,content TEXT,is_approved INTEGER,is_global INTEGER,updated_at TEXT)')
            db.execute('INSERT INTO memories VALUES(1,?,0,0,?)', ('old', 'now'))
        self.helper.do('memory_update', {'id': 1, 'content': "new'; DROP TABLE memories;--", 'is_approved': True})
        with sqlite3.connect(folder / 'meme_database.db') as db:
            self.assertEqual(db.execute('SELECT content,is_approved FROM memories').fetchone(), ("new'; DROP TABLE memories;--", 1))

    def test_model_selection_is_fixed_and_missing_key_rejected(self):
        with self.assertRaises(ValueError):
            self.helper.do('model_select', {'provider': 'https://evil.invalid'})
        with self.assertRaises(ValueError):
            self.helper.do('model_select', {'provider': 'kimi'})
        self.helper.do('model_select', {'provider': 'deepseek'})
        self.assertEqual(self.helper.do('models', {})['selected']['provider'], 'deepseek')
        self.helper.do('model_select', {'provider': 'deepseek', 'thinking': False})
        self.assertFalse(self.helper.do('models', {})['models'][0]['thinking'])


if __name__ == '__main__':
    unittest.main()
