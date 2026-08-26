# -*- coding: utf-8 -*-
"""ConsequenceEngine 客观后果反馈测试(IVD 重构)

覆盖 mavisframework.runtime.consequence.ConsequenceEngine.feedback:
- 空描述 → 空反馈
- 无约束 → 空反馈(制度未期望,不参与)
- 只对约束内目标反馈(约束外目标不进入)
- 正向关键词命中 → 反馈 > 0.5×权重
- 反向关键词命中 → 反馈 < 0.5×权重
- 约束权重加权:同表现下权重高者反馈高(专家调权 → 倾向侧重变化)
"""
import pytest

from mavisframework.runtime.consequence import ConsequenceEngine


class _FakeAgent:
    """最小 agent 桩:只提供 get_constraints / role_type"""

    def __init__(self, constraints, role_type="user"):
        self._constraints = constraints
        self.role_type = role_type

    def get_constraints(self):
        return dict(self._constraints)


class TestConsequenceEngine:
    def setup_method(self):
        self.engine = ConsequenceEngine(volatility=0.5, market_trend="volatile")

    def test_empty_action_no_feedback(self):
        agent = _FakeAgent({"Maximize Returns": 1.0})
        assert self.engine.feedback(agent, "") == {}
        assert self.engine.feedback(agent, None) == {}

    def test_no_constraints_no_feedback(self):
        # 制度未给该角色设期望 → 无反馈(约束外目标不参与)
        assert self.engine.feedback(None, "buy more stock to maximize returns") == {}
        assert self.engine.feedback(_FakeAgent({}), "buy more stock to maximize returns") == {}

    def test_no_keyword_hit_neutral_feedback_user(self):
        # user:未命中 → 中性 0.5(不乘权重)→ 起点=均匀(从体验形成)
        agent = _FakeAgent({"Serve Users": 0.6, "Risk Alerting": 0.4}, role_type="user")
        fb = self.engine.feedback(agent, "compare yield trends and performance metrics")
        assert fb == {"Serve Users": 0.5, "Risk Alerting": 0.5}

    def test_no_keyword_hit_neutral_feedback_ai_tool(self):
        # ai_tool:未命中 → 0.5×权重 → 归一化后起点=约束(制度内建)
        agent = _FakeAgent({"Serve Users": 0.6, "Risk Alerting": 0.4}, role_type="ai_tool")
        fb = self.engine.feedback(agent, "compare yield trends and performance metrics")
        assert fb == {"Serve Users": 0.3, "Risk Alerting": 0.2}  # 0.5×权重

    def test_only_constrained_goals_feedback(self):
        # 约束只含 Risk Aversion:行动命中 Maximize Returns 关键词也不反馈它
        agent = _FakeAgent({"Risk Aversion": 1.0})
        fb = self.engine.feedback(agent, "buy more stock aggressively to maximize returns")
        assert "Maximize Returns" not in fb
        assert "Risk Aversion" in fb  # aggressive → Risk Aversion 反向命中

    def test_positive_keyword_raises_feedback(self):
        agent = _FakeAgent({"Maximize Returns": 1.0})
        fb = self.engine.feedback(agent, "buy more stock to maximize returns and profit")
        # 含 Maximize Returns 正向关键词(buy/return/profit),权重 1.0
        assert fb.get("Maximize Returns", 0) > 0.5

    def test_negative_keyword_lowers_feedback(self):
        agent = _FakeAgent({"Maximize Returns": 1.0})
        fb = self.engine.feedback(agent, "reduce exposure and hedge against risk")
        # Maximize Returns 反向关键词(hedge/reduce)命中 → 反馈 < 0.5
        assert fb.get("Maximize Returns", 0) < 0.5

    def test_weight_scales_feedback(self):
        # 同一行动,权重 0.9 vs 0.5:前者反馈更高(专家调权 → 倾向侧重变化)
        agent_high = _FakeAgent({"Risk Aversion": 0.9})
        agent_low = _FakeAgent({"Risk Aversion": 0.5})
        action = "hedge against portfolio risk to protect capital"
        fb_high = self.engine.feedback(agent_high, action)
        fb_low = self.engine.feedback(agent_low, action)
        assert fb_high["Risk Aversion"] > fb_low["Risk Aversion"]

    def test_feedback_clamped_to_unit_range(self):
        agent = _FakeAgent({"Maximize Returns": 1.0})
        fb = self.engine.feedback(
            agent,
            "buy buy buy add add add chase chase return return profit profit gain gain",
        )
        for v in fb.values():
            assert 0.0 <= v <= 1.0

    def test_feedback_deterministic(self):
        agent = _FakeAgent({"Risk Alerting": 1.0})
        fb1 = self.engine.feedback(agent, "alert the client about portfolio risk")
        fb2 = self.engine.feedback(agent, "alert the client about portfolio risk")
        assert fb1 == fb2
        assert fb1.get("Risk Alerting", 0) > 0.5
