# -*- coding: utf-8 -*-
"""ConsequenceEngine 客观后果反馈测试(IVD 重构)

覆盖 mavisframework.runtime.consequence.ConsequenceEngine.feedback:
- 空描述 → 空反馈
- 正向关键词命中 → 反馈 > 0.5
- 反向关键词命中 → 反馈 < 0.5
- 反馈独立于约束(不因任何角色偏好而偏袒)
"""
import pytest

from mavisframework.runtime.consequence import ConsequenceEngine


class TestConsequenceEngine:
    def setup_method(self):
        self.engine = ConsequenceEngine(volatility=0.5, market_trend="volatile")

    def test_empty_action_no_feedback(self):
        assert self.engine.feedback(None, "") == {}
        assert self.engine.feedback(None, None) == {}

    def test_positive_keyword_raises_feedback(self):
        fb = self.engine.feedback(None, "buy more stock to maximize returns and profit")
        # 含 Maximize Returns 正向关键词(buy/return/profit)
        assert fb.get("Maximize Returns", 0) > 0.5

    def test_negative_keyword_lowers_feedback(self):
        fb = self.engine.feedback(None, "reduce exposure and hedge against risk")
        # Risk Aversion 正向(hedge/reduce/risk),Maximize Returns 反向(hedge/reduce)
        assert fb.get("Risk Aversion", 0) > 0.5
        assert fb.get("Maximize Returns", 0) < 0.5

    def test_feedback_clamped_to_unit_range(self):
        # 大量关键词命中不越界
        fb = self.engine.feedback(
            None,
            "buy buy buy add add add chase chase return return profit profit gain gain",
        )
        for v in fb.values():
            assert 0.0 <= v <= 1.0

    def test_feedback_not_biased_by_constraints(self):
        # 同一行动,反馈应只由文本决定(engine 不读取任何角色约束)
        fb1 = self.engine.feedback(None, "alert the client about portfolio risk")
        fb2 = self.engine.feedback(None, "alert the client about portfolio risk")
        assert fb1 == fb2
        # Risk Alerting 应被正向触发
        assert fb1.get("Risk Alerting", 0) > 0.5
