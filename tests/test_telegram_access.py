import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location(
    'telegram_access_policy', Path(__file__).parents[1] / 'Services/telegram_access.py')
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)


def message(user_id=101, text='/start', kind='private', **sender):
    return {'from': {'id': user_id, **sender}, 'chat': {'id': user_id, 'type': kind}, 'text': text}


class TelegramAccessTests(unittest.TestCase):
    def test_empty_defaults_to_deny(self):
        access = policy.TelegramAccess(policy.parse_allowed_ids(''))
        self.assertFalse(access.authorized(message()))

    def test_whitespace_commas_and_duplicates(self):
        self.assertEqual(policy.parse_allowed_ids('101, 202\n101'), frozenset({101, 202}))

    def test_invalid_config_rejected(self):
        for value in ('*', '@someone', '101,invalid', '-1', '0', '99999999999999999999'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                policy.parse_allowed_ids(value)

    def test_sender_id_not_username_or_chat_id(self):
        access = policy.TelegramAccess({101})
        self.assertTrue(access.authorized(message(101)))
        stranger = message(202, username='101')
        stranger['chat']['id'] = 101
        self.assertFalse(access.authorized(stranger))
        self.assertFalse(access.authorized(message('101')))
        self.assertFalse(access.authorized(message(True)))
        self.assertFalse(access.authorized({'chat': {'id': 101}}))

    def test_bots_denied_even_with_listed_id(self):
        self.assertFalse(policy.TelegramAccess({101}).authorized(message(is_bot=True)))

    def test_start_notice_does_not_grant_access(self):
        access = policy.TelegramAccess(set())
        self.assertIn('101', access.denial_notice(message(), 0))
        self.assertFalse(access.authorized(message()))
        self.assertIsNone(access.denial_notice(message(text='你好'), 100))

    def test_no_notice_for_group_or_authorized_user(self):
        self.assertIsNone(policy.TelegramAccess(set()).denial_notice(message(kind='group'), 0))
        self.assertIsNone(policy.TelegramAccess({101}).denial_notice(message(), 0))

    def test_notice_throttle_and_bounded_state(self):
        access = policy.TelegramAccess(set())
        self.assertIsNotNone(access.denial_notice(message(), 0))
        self.assertIsNone(access.denial_notice(message(), 59))
        self.assertIsNone(access.denial_notice(message(202), 1))
        self.assertIsNotNone(access.denial_notice(message(), 60))
        for index in range(600):
            access.denial_notice(message(1000 + index), 100 + index * 6)
        self.assertEqual(len(access.notices), 512)


if __name__ == '__main__':
    unittest.main()
