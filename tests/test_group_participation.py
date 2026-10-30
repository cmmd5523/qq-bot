import importlib.util
from pathlib import Path
import sys
import unittest

MODULE = Path(__file__).parents[1] / 'Services/group_participation.py'
spec = importlib.util.spec_from_file_location('participation_scoring', MODULE)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
ParticipationState = module.ParticipationState


class ParticipationTests(unittest.TestCase):
    def discussion(self):
        state = ParticipationState()
        state.observe('a', '服务器部署后网页一直打不开', 100)
        state.observe('b', '网页打不开可能是端口没有放行', 110)
        state.observe('a', '服务器网页打不开应该怎么排查？', 120)
        return state

    def test_relevant_question_crosses_threshold(self):
        state = self.discussion()
        self.assertGreaterEqual(state.score(120)[0], 65)
        self.assertTrue(state.available(120))

    def test_single_speaker_is_not_auto_answered(self):
        state = self.discussion()
        state.turns = module.deque(
            [module.Turn('a', turn.text, turn.at) for turn in state.turns], maxlen=30)
        self.assertEqual(state.score(120)[0], 0)

    def test_related_opinion_can_invite_participation(self):
        state = self.discussion()
        state.observe('b', '感觉这个服务器网页的问题挺有意思', 130)
        self.assertGreaterEqual(state.score(130)[0], 65)

    def test_unrelated_question_stays_below_threshold(self):
        state = ParticipationState()
        state.observe('a', '今天中午准备吃面条', 100)
        state.observe('b', '刚刚看完这场电影', 110)
        state.observe('a', '服务器为什么会突然重启？', 120)
        self.assertLess(state.score(120)[0], 65)

    def test_duplicate_and_links_are_ignored(self):
        state = self.discussion()
        state.observe('b', '服务器网页打不开应该怎么排查？', 130)
        self.assertEqual(state.score(130)[0], 0)
        state.observe('b', '这个网站打不开 https://example.com 为什么？', 140)
        self.assertEqual(state.score(140)[0], 0)

    def test_context_expires_and_is_bounded(self):
        state = self.discussion()
        state.expire(301)
        self.assertEqual(len(state.turns), 0)
        for index in range(100):
            state.observe(str(index % 2), '一些群聊话题', 400 + index)
        self.assertEqual(len(state.turns), 30)

    def test_direct_mentions_and_sent_reply_enforce_cooldown(self):
        state = self.discussion()
        state.observe('b', '请机器人回答一下', 130, directed=True)
        self.assertFalse(state.available(309))
        self.assertTrue(state.available(310))
        state.last_sent = 310
        self.assertFalse(state.available(489))
        self.assertTrue(state.available(490))

    def test_failed_attempts_are_also_rate_limited(self):
        state = ParticipationState()
        self.assertTrue(state.reserve(0))
        self.assertFalse(state.reserve(29))
        for timestamp in (30, 60, 90, 120, 150):
            self.assertTrue(state.reserve(timestamp))
        self.assertFalse(state.reserve(180))
        self.assertTrue(state.reserve(3600))

    def test_transcript_uses_temporary_labels(self):
        state = self.discussion()
        transcript = state.transcript()
        self.assertEqual(transcript[0]['speaker'], transcript[-1]['speaker'])
        self.assertNotEqual(transcript[0]['speaker'], transcript[1]['speaker'])
        self.assertNotIn('user', transcript[0])


if __name__ == '__main__':
    unittest.main()
