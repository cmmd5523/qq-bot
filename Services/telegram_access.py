"""Fail-closed Telegram sender allowlist; no credentials or IDs in source."""
from collections import OrderedDict
import re


def parse_allowed_ids(value):
    tokens = [x for x in re.split(r'[,\s]+', value.strip()) if x]
    if any(not re.fullmatch(r'[1-9][0-9]{0,18}', token) or int(token) > 2**63 - 1 for token in tokens):
        raise ValueError('Telegram allowlist requires positive numeric user IDs')
    return frozenset(int(token) for token in tokens)


class TelegramAccess:
    def __init__(self, allowed_ids):
        self.allowed_ids = frozenset(allowed_ids)
        self.notices = OrderedDict()
        self.last_notice = float('-inf')

    def authorized(self, message):
        sender = message.get('from', {})
        user_id = sender.get('id')
        return (type(user_id) is int and user_id > 0
                and not sender.get('is_bot') and user_id in self.allowed_ids)

    def denial_notice(self, message, now):
        sender = message.get('from', {})
        user_id = sender.get('id')
        if (self.authorized(message) or type(user_id) is not int or user_id <= 0
                or sender.get('is_bot') or message.get('chat', {}).get('type') != 'private'
                or not re.match(r'^/(?:start|help)(?:@\w+)?(?:\s|$)', message.get('text', ''))):
            return None
        if now - self.notices.get(user_id, float('-inf')) < 60 or now - self.last_notice < 5:
            return None
        self.notices[user_id] = now
        self.notices.move_to_end(user_id)
        while len(self.notices) > 512:
            self.notices.popitem(last=False)
        self.last_notice = now
        return f'未授权。你的 Telegram 用户 ID：{user_id}\n请让机器人管理员将该数字加入白名单。'
