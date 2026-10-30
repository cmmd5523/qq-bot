"""Bounded, in-memory scoring for opt-in group participation."""
from collections import deque
from dataclasses import dataclass
import re

WINDOW_SECONDS = 180
COOLDOWN_SECONDS = 180
HOURLY_LIMIT = 6


def terms(text):
    result = set(re.findall(r'[a-z][a-z0-9_]{2,}', text.lower()))
    for chunk in re.findall(r'[\u4e00-\u9fff]+', text):
        result.update(chunk[i:i + 2] for i in range(len(chunk) - 1))
    return result


@dataclass(frozen=True)
class Turn:
    user: str
    text: str
    at: float


class ParticipationState:
    def __init__(self):
        self.turns = deque(maxlen=30)
        self.attempts = deque()
        self.last_sent = float('-inf')
        self.last_direct = float('-inf')
        self.last_attempt = float('-inf')
        self.version = 0

    def observe(self, user, text, now, directed=False):
        self.version += 1
        if directed:
            self.last_direct = now
        text = text.strip()
        if text and not text.startswith(('/', '!', '／', '！', '#')):
            self.turns.append(Turn(str(user), text[:480], now))
        self.expire(now)

    def expire(self, now):
        while self.turns and now - self.turns[0].at > WINDOW_SECONDS:
            self.turns.popleft()
        while self.attempts and now - self.attempts[0] >= 3600:
            self.attempts.popleft()

    def score(self, now):
        self.expire(now)
        if len(self.turns) < 3:
            return 0, ['context_too_short']
        current = self.turns[-1]
        text = current.text
        if len(text) < 8 or re.fullmatch(r'[\W_哈呵嘿嗯哦啊好笑]+', text):
            return 0, ['low_information']
        if 'http://' in text or 'https://' in text or '[CQ:' in text:
            return 0, ['link_or_protocol']
        if any(turn.text == text for turn in list(self.turns)[:-1]):
            return 0, ['duplicate']
        if len({turn.user for turn in self.turns}) < 2:
            return 0, ['single_speaker']
        score, reasons = 10, ['substantive_message:+10']
        if re.search(r'[?？]|怎么|为什么|如何|怎么办|有没有|谁知道|请教|求助|能不能', text):
            score += 35
            reasons.append('question:+35')
        elif re.search(r'感觉|我觉得|建议|纠结|求推荐|有意思|不知道|大家觉得', text):
            score += 25
            reasons.append('discussion_invitation:+25')
        prior = set()
        for turn in list(self.turns)[-7:-1]:
            prior.update(terms(turn.text))
        overlap = len(terms(text) & prior)
        if overlap >= 2:
            score += 25
            reasons.append('topic_overlap:+25')
        elif overlap:
            score += 10
            reasons.append('topic_overlap:+10')
        if len({turn.user for turn in list(self.turns)[-8:]}) >= 2:
            score += 10
            reasons.append('multi_speaker:+10')
        return min(score, 100), reasons

    def available(self, now):
        self.expire(now)
        return (now - self.last_sent >= COOLDOWN_SECONDS
                and now - self.last_direct >= COOLDOWN_SECONDS
                and now - self.last_attempt >= 30
                and len(self.attempts) < HOURLY_LIMIT)

    def reserve(self, now):
        if not self.available(now):
            return False
        self.last_attempt = now
        self.attempts.append(now)
        return True

    def transcript(self):
        labels = {}
        result = []
        for turn in self.turns:
            label = labels.setdefault(turn.user, f'成员{len(labels) + 1}')
            result.append({'speaker': label, 'text': turn.text})
        return result
