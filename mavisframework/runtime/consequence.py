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
                 scorer=None, softmax_temp: float = 1.0):
        """volatility: 市场波动度(0-1);market_trend: volatile/stable
        scorer: GoalScorer 实例(可注入,便于测试);缺省懒加载
        softmax_temp: 相对优势 softmax 温度(>1 平滑对比度,<1 放大)
        """
        self.volatility = volatility
        self.market_trend = market_trend
        self._scorer = scorer
        self.softmax_temp = softmax_temp
        # embedding 稳定性监控(可观测性):
        # - total_calls: 反馈计算总次数
        # - degraded_calls: 降级(embedding 不可用)次数
        # - last_error: 最近一次失败原因
        self.total_calls = 0
        self.degraded_calls = 0
        self.last_error = ""

    def health(self) -> dict:
        """embedding 稳定性健康度(供监控/前端展示)

        返回 {total_calls, degraded_calls, degrade_rate, last_error}
        degrade_rate = 降级占比;持续 > 0 说明 embedding 服务不稳定,
        倾向曲线可能出现"平段"(退到约束线)。
        """
        rate = (self.degraded_calls / self.total_calls) if self.total_calls else 0.0
        return {
            "total_calls": self.total_calls,
            "degraded_calls": self.degraded_calls,
            "degrade_rate": round(rate, 4),
            "last_error": self.last_error,
        }

    def _get_scorer(self):
        if self._scorer is None:
            from mavisframework.runtime.goal_scorer import GoalScorer

            self._scorer = GoalScorer()
        return self._scorer

    def _degrade(self, error: str, constraints: Dict) -> Dict[str, float]:
        """降级路径:embedding 不可用 → 中性反馈(= 约束权重)

        记录失败计数与原因,供 health() 暴露(不静默)。
        V4:返回 {g: w}(而非 0.5×w)——与正常反馈(softmax 占比 × w)
        量纲一致(Σ=Σw)。旧 0.5×w 在窗口混合时整体被低估,
        "倾向退到制度期望"的语义不成立;改 w 后降级即退到约束线。
        """
        self.degraded_calls += 1
        self.last_error = error
        return {g: w for g, w in constraints.items() if w and w > 0}

    def feedback(self, agent, action_desc: str) -> Dict[str, float]:
        """对一次行动给出各目标的结果反馈(0-1)

        IVD 语义(2026-08 定版):
        - 反馈的目标集 = 该角色的治理约束(governance.json 中的期望目标),
          约束外的目标不进入反馈——制度决定"该角色应该关心什么"。
        - 反馈值 = 行动文本与目标文本的 embedding 余弦相似度 × 约束权重,
          专家调权重 → 反馈侧重变化 → 倾向滞后收敛(内化证据)。
        - 客观性保留在"测量方法"上(embedding 相似度,不因人而异),
          不独立的是"目标集由制度定"。
        - embedding 不可用(网络/服务异常)时降级为中性反馈
          0.5×权重(倾向退到制度期望),并记录失败(health() 可观测)。
        """
        self.total_calls += 1
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
                return self._degrade("action embedding returned None", constraints)
            sims = {}
            for goal, weight in constraints.items():
                if not weight or weight <= 0:
                    continue
                g_vec = scorer.embed(goal)
                if g_vec is None:
                    continue
                sim = scorer._cosine(a_vec, g_vec)
                # V8:保留负相似度(不截断到 [0,1])——行动明确违背某目标时,
                # 负 sim 经 softmax 后占比小,该目标反馈弱(内化弱),而非
                # 截断为 0 后"无惩罚"、倾向不降反稳
                sims[goal] = sim
            if not sims:
                return self._degrade("all goal embeddings failed", constraints)
            # 相对优势:softmax(sim) —— 天然处理负值、自动归一化(Σ=1)、
            # 保留相对差异。温度可调(softmax_temp,>1 平滑对比度)。
            # V8:替代旧的线性 sim/Σsim(负值会扭曲分母,且截断丢反向信号)。
            import math as _m
            temp = float(getattr(self, "softmax_temp", 1.0))
            exp_v = {g: _m.exp(s / temp) for g, s in sims.items()}
            denom = sum(exp_v.values())
            if denom <= 1e-12:
                return self._degrade("softmax denominator zero", constraints)
            out = {}
            for goal, weight in constraints.items():
                if weight <= 0 or goal not in exp_v:
                    continue
                out[goal] = (exp_v[goal] / denom) * weight
            return out
        except Exception as e:
            # 降级:embedding 异常 → 中性反馈(= 约束权重),倾向退到制度期望
            return self._degrade(str(e), constraints)
