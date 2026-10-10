"""T20 动态预警信号组合与持续周期逻辑验证。

真实连续资料与 D04/D05 规则由 T32 验证；本文件验证组合与周期状态转换。
运行：backend/.venv/bin/python backend/manage.py test tests.test_T20 --keepdb
"""
from django.test import SimpleTestCase

from alerting.episodes import decide_episode
from alerting.signals import evaluate_combination


class CombinationTest(SimpleTestCase):
    def test_all(self):
        self.assertEqual(evaluate_combination("all", ["met", "met"]), "met")
        self.assertEqual(evaluate_combination("all", ["met", "not_met"]), "not_met")
        self.assertEqual(evaluate_combination("all", ["met", "unknown"]), "unknown")

    def test_any(self):
        self.assertEqual(evaluate_combination("any", ["not_met", "met"]), "met")
        self.assertEqual(evaluate_combination("any", ["not_met", "not_met"]), "not_met")
        self.assertEqual(evaluate_combination("any", ["not_met", "unknown"]), "unknown")

    def test_at_least(self):
        self.assertEqual(evaluate_combination("at_least:2", ["met", "met", "not_met"]), "met")
        self.assertEqual(evaluate_combination("at_least:2", ["met", "not_met", "not_met"]), "not_met")
        self.assertEqual(evaluate_combination("at_least:2", ["met", "unknown", "not_met"]), "unknown")


class EpisodeTransitionTest(SimpleTestCase):
    def test_closed_met_creates_event(self):
        decision = decide_episode(current_status=None, signal_state="met")
        self.assertEqual(decision.action, "create_event")
        self.assertEqual(decision.next_status, "open")

    def test_open_met_continues(self):
        decision = decide_episode(current_status="open", signal_state="met")
        self.assertEqual(decision.action, "continue_episode")
        self.assertEqual(decision.next_status, "open")

    def test_open_unknown_does_not_close(self):
        decision = decide_episode(current_status="open", signal_state="unknown")
        self.assertEqual(decision.action, "no_event")
        self.assertEqual(decision.next_status, "open")

    def test_open_not_met_closes_on_reset(self):
        decision = decide_episode(current_status="open", signal_state="not_met", reset_met=True)
        self.assertEqual(decision.action, "close_episode")
        self.assertEqual(decision.next_status, "closed")

    def test_open_not_met_stays_open_without_reset(self):
        decision = decide_episode(current_status="open", signal_state="not_met", reset_met=False)
        self.assertEqual(decision.action, "no_event")
        self.assertEqual(decision.next_status, "open")

    def test_closed_not_met_no_event(self):
        decision = decide_episode(current_status="closed", signal_state="not_met")
        self.assertEqual(decision.action, "no_event")
        self.assertEqual(decision.next_status, "closed")

    def test_open_met_re_trigger_creates_new_sequence(self):
        decision = decide_episode(current_status="open", signal_state="met", re_trigger=True)
        self.assertEqual(decision.action, "create_event")
        self.assertEqual(decision.next_status, "open")
