# -*- coding: utf-8 -*-
"""ConsequenceEngine 客观后果反馈测试(IVD:embedding 相似度)

覆盖 mavisframework.runtime.consequence.ConsequenceEngine.feedback:
- 空描述 → 空反馈
- 无约束 → 空反馈(制度未期望,不参与)
- 只对约束内目标反馈(约束外目标不进入)
- 反馈 = embedding 相似度 × 约束权重(注入假 scorer 验证)
- 权重加权:同相似度下权重高者反馈高(专家调权 → 倾向侧重变化)
- embedding 不可用 → 降级中性反馈(0.5×权重)
"""
import pytest

from mavisframework.runtime.consequence import ConsequenceEngine


class _FakeAgent:
    """最小 agent 桩:只提供 get_constraints"""

    def __init__(self, constraints):
        self._constraints = constraints

    def get_constraints(self):
        return dict(self._constraints)


class _FakeScorer:
    """假 GoalScorer:embed 返回可配置向量,cosine 用真实公式"""

    def __init__(self, action_vec, goal_vecs):
        self._action = action_vec
        self._goals = goal_vecs
        self.calls = 0

    def embed(self, text):
        self.calls += 1
        if text in self._goals:
            return self._goals[text]
        return self._action  # 行动文本返回行动向量

    @staticmethod
    def _cosine(a, b):
        import math
        if not a or not b or len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(y * y for y in b))
        if na == 0 or nb == 0:
            return 0.0
        return dot / (na * nb)


class TestConsequenceEngine:
    def setup_method(self):
        self.engine = ConsequenceEngine(volatility=0.5, market_trend="volatile")

    def test_empty_action_no_feedback(self):
        agent = _FakeAgent({"Maximize Returns": 1.0})
        assert self.engine.feedback(agent, "") == {}
        assert self.engine.feedback(agent, None) == {}

    def test_no_constraints_no_feedback(self):
        # 制度未给该角色设期望 → 无反馈
        assert self.engine.feedback(None, "buy more stock to maximize returns") == {}
        assert self.engine.feedback(_FakeAgent({}), "buy more stock") == {}

    def test_only_constrained_goals_feedback(self):
        # 约束只含 Risk Aversion:返回里不应出现约束外目标
        scorer = _FakeScorer(
            action_vec=[1.0, 0.0],
            goal_vecs={"Risk Aversion": [1.0, 0.0], "Maximize Returns": [0.0, 1.0]},
        )
        self.engine._scorer = scorer
        agent = _FakeAgent({"Risk Aversion": 1.0})
        fb = self.engine.feedback(agent, "hedge against risk")
        assert set(fb.keys()) == {"Risk Aversion"}

    def test_feedback_uses_similarity_times_weight(self):
        # 相对优势:sim A=1.0, B=0.0 → 占比 A=1.0, B=0.0 × 权重 [0.7, 0.3]
        scorer = _FakeScorer(
            action_vec=[1.0, 0.0],
            goal_vecs={"A": [1.0, 0.0], "B": [0.0, 1.0]},
        )
        self.engine._scorer = scorer
        agent = _FakeAgent({"A": 0.7, "B": 0.3})
        fb = self.engine.feedback(agent, "do A-like thing")
        assert fb["A"] == pytest.approx(1.0 * 0.7)
        assert fb["B"] == pytest.approx(0.0 * 0.3)

    def test_relative_advantage_amplifies_difference(self):
        # 构造 sim: A=0.5, B=0.55 → 占比 0.476 / 0.524,区分度放大且保留中间态
        # 用 action=[1,0,0], A=[0.5, sqrt(0.75), 0] → cos=0.5
        import math
        s3 = math.sqrt(0.75)
        scorer = _FakeScorer(
            action_vec=[1.0, 0.0, 0.0],
            goal_vecs={"A": [0.5, s3, 0.0], "B": [0.55, 0.0, math.sqrt(1 - 0.55**2)]},
        )
        self.engine._scorer = scorer
        agent = _FakeAgent({"A": 0.5, "B": 0.5})
        fb = self.engine.feedback(agent, "x")
        # 占比: A=0.5/1.05=0.476, B=0.55/1.05=0.524;×权重0.5
        assert fb["A"] == pytest.approx((0.5 / 1.05) * 0.5, abs=1e-6)
        assert fb["B"] == pytest.approx((0.55 / 1.05) * 0.5, abs=1e-6)

    def test_weight_scales_feedback(self):
        # 同一行动,权重 0.9 vs 0.5:高权重者反馈更高
        scorer = _FakeScorer(action_vec=[1.0, 0.0], goal_vecs={"G": [1.0, 0.0]})
        self.engine._scorer = scorer
        fb_high = self.engine.feedback(_FakeAgent({"G": 0.9}), "act")
        fb_low = self.engine.feedback(_FakeAgent({"G": 0.5}), "act")
        assert fb_high["G"] > fb_low["G"]

    def test_embedding_failure_falls_back_neutral(self):
        # scorer.embed 抛异常 → 中性反馈 0.5×权重
        class _Broken:
            def embed(self, text):
                raise RuntimeError("ollama down")

        self.engine._scorer = _Broken()
        agent = _FakeAgent({"Serve Users": 0.6, "Risk Alerting": 0.4})
        fb = self.engine.feedback(agent, "anything")
        assert fb == {"Serve Users": 0.3, "Risk Alerting": 0.2}

    def test_health_tracks_degradation(self):
        # 健康度:成功调用不降级;embedding 失败计入 degraded_calls 与 last_error
        scorer = _FakeScorer(action_vec=[1.0, 0.0], goal_vecs={"G": [1.0, 0.0]})
        self.engine._scorer = scorer
        agent = _FakeAgent({"G": 1.0})
        self.engine.feedback(agent, "ok")  # 成功
        h = self.engine.health()
        assert h["total_calls"] == 1
        assert h["degraded_calls"] == 0
        assert h["degrade_rate"] == 0.0

        class _Broken:
            def embed(self, text):
                raise RuntimeError("ollama down")

        self.engine._scorer = _Broken()
        self.engine.feedback(agent, "fail")  # 失败 → 降级
        h2 = self.engine.health()
        assert h2["total_calls"] == 2
        assert h2["degraded_calls"] == 1
        assert h2["degrade_rate"] == pytest.approx(0.5)
        assert "ollama down" in h2["last_error"]
