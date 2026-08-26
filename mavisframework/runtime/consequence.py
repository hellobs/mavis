"""mavisframework.runtime.consequence — 客观后果反馈(IVD 重构)

为"AI 体验到什么行动成功"提供轻量的结果反馈:
- 反馈目标集 = 该角色的治理约束(制度期望),约束外目标不参与
- 反馈值 = 行动文本与目标文本的 **embedding 语义相似度** × 约束权重
  (客观测量:这个行动在多大程度上体现该价值;约束权重 = 制度重视度)
- 反馈用于 Agent.observe_consequence → 更新 value_tendency(内化)

2026-08 定版:
- 目标集由制度约束决定(约束外目标不进入反馈)
- 反馈 = embedding 相似度 min-max 对比拉伸 × 约束权重
  (原始余弦相似度挤在 0.35-0.65,拉伸放大相对差异,曲线才可见起伏)
- 专家调权重 → 反馈侧重变化 → 倾向滞后收敛(内化证据)
- 起点由 Agent 的 initial_tendency(人物底色)决定,ai_tool 无底色时
  从第一次体验起由 embedding 相似度塑造(其行动天然贴合制度目标)
- embedding 不可用时降级为中性反馈(不引入虚假区分度)

相比关键词启发式:embedding 相似度连续、有区分度、语义更准——
倾向曲线不再被"命中/未命中"二分锁死(平行),而随行动语义自然起伏。
"""
from typing import Dict


class ConsequenceEngine:
    """客观后果判定:行动文本 → 各目标的结果反馈"""

    def __init__(self, volatility: float = 0.5, market_trend: str = "volatile",
                 scorer=None):
        """volatility: 市场波动度(0-1);market_trend: volatile/stable
        scorer: GoalScorer 实例(可注入,便于测试);缺省懒加载
        """
        self.volatility = volatility
        self.market_trend = market_trend
        self._scorer = scorer

    def _get_scorer(self):
        if self._scorer is None:
            from mavisframework.runtime.goal_scorer import GoalScorer

            self._scorer = GoalScorer()
        return self._scorer

    def feedback(self, agent, action_desc: str) -> Dict[str, float]:
        """对一次行动给出各目标的结果反馈(0-1)

        IVD 语义(2026-08 定版):
        - 反馈的目标集 = 该角色的治理约束(governance.json 中的期望目标),
          约束外的目标不进入反馈——制度决定"该角色应该关心什么"。
        - 反馈值 = 行动文本与目标文本的 embedding 余弦相似度 × 约束权重,
          专家调权重 → 反馈侧重变化 → 倾向滞后收敛(内化证据)。
        - 客观性保留在"测量方法"上(embedding 相似度,不因人而异),
          不独立的是"目标集由制度定"。
        - embedding 不可用(网络/服务异常)时返回中性反馈:
          0.5×权重(与约束加权同尺度),倾向维持当前水平,不引入虚假波动。
        """
        text = (action_desc or "").strip()
        if not text:
            return {}
        # 约束目标集:该角色被制度期望关注的价值(缺约束 → 无反馈)
        constraints = {}
        try:
            if agent is not None:
                constraints = agent.get_constraints() or {}
        except Exception:
            constraints = {}
        if not constraints:
            return {}
        # 目标文本预先 embed(只调一次/目标,结果缓存于 scorer)
        try:
            scorer = self._get_scorer()
            a_vec = scorer.embed(text)
            if a_vec is None:
                raise RuntimeError("action embedding failed")
            sims = {}
            for goal, weight in constraints.items():
                if not weight or weight <= 0:
                    continue
                g_vec = scorer.embed(goal)
                if g_vec is None:
                    continue
                sim = scorer._cosine(a_vec, g_vec)
                # 软映射:embedding 余弦相似度对"行动 vs 目标短语"天然挤在
                # 0.35-0.65 区间,线性映射到 [0.1, 0.9],保留连续性与中间态
                # (不做 min-max 拉伸——那会制造 0/1 极化,倾向被钉死)
                v = 0.1 + (max(0.0, min(1.0, sim)) - 0.35) / 0.3 * 0.8
                v = max(0.1, min(0.9, v))
                sims[goal] = v
            if not sims:
                return {g: 0.5 * w for g, w in constraints.items() if w and w > 0}
            out = {}
            for goal, weight in constraints.items():
                if weight <= 0 or goal not in sims:
                    continue
                out[goal] = sims[goal] * weight
            return out
        except Exception:
            # 降级:中性反馈(0.5×权重),倾向维持当前水平
            return {g: 0.5 * w for g, w in constraints.items() if w and w > 0}
