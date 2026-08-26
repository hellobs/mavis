"""mavisframework.runtime.consequence — 客观后果反馈(IVD 重构)

为"AI 体验到什么行动成功"提供轻量的结果反馈:
- 反馈目标集 = 该角色的治理约束(制度期望),约束外目标不参与
- 反馈值 = 客观表现(关键词命中)× 约束权重(制度重视度)
- 反馈用于 Agent.observe_consequence → 更新 value_tendency(内化)

2026-08 定版:约束权重参与反馈加权——专家调整约束会改变反馈侧重,
倾向随之滞后收敛(内化/习惯的证据)。客观性在测量方法,目标集由制度定。
当前为简化版:基于关键词的启发式判定,后续可扩展为市场模型/事件驱动。
"""
from typing import Dict, Callable, Optional

# 目标语义关键词(用于启发式判定行动与该目标的相关性与方向)
# 每个目标 -> (正向关键词, 反向关键词)
_GOAL_KEYWORDS = {
    "Maximize Returns": (["buy", "add", "aggress", "chase", "position", "leverag", "return", "gain", "profit"],
                         ["sell", "reduce", "hedge", "cash", "protect", "avoid", "risk"]),
    "Risk Aversion": (["risk", "hedge", "reduce", "protect", "drawdown", "stress", "avoid", "safe", "conservative", "stop-loss"],
                      ["aggress", "chase", "leverag", "gamble", "reckless"]),
    "Serve Users": (["user", "client", "advise", "respond", "help", "service", "interact", "consult"],
                    ["ignore", "internal", "self"]),
    "Risk Alerting": (["alert", "warn", "risk", "monitor", "warning", "flag", "report"],
                      ["silent", "ignore"]),
    "Steady Returns": (["steady", "stable", "long-term", "value", "diversif", "consistent"],
                       ["volatile", "speculat", "gamble"]),
    "Business Advancement": (["advance", "progress", "grow", "expand", "business", "initiative"],
                             ["stagnat", "retreat"]),
    "Strategy Stability": (["stable", "consistent", "strategy", "robust", "discipline"],
                           ["random", "impulsive", "overfit"]),
    "Research Rigor": (["research", "cross-check", "validate", "verify", "data", "evidence", "rigor", "audit"],
                       ["guess", "assume", "sloppy"]),
    "Timeliness": (["now", "immediately", "urgent", "timely", "prompt", "quick", "real-time"],
                   ["delay", "later", "procrastinat"]),
}


class ConsequenceEngine:
    """客观后果判定:行动文本 → 各目标的结果反馈"""

    def __init__(self, volatility: float = 0.5, market_trend: str = "volatile"):
        """volatility: 市场波动度(0-1);market_trend: volatile/stable"""
        self.volatility = volatility
        self.market_trend = market_trend

    def feedback(self, agent, action_desc: str) -> Dict[str, float]:
        """对一次行动给出各目标的结果反馈(0-1)

        IVD 语义(2026-08 定版):
        - 反馈的目标集 = 该角色的治理约束(governance.json 中的期望目标),
          约束外的目标不进入反馈——制度决定"该角色应该关心什么"。
        - 反馈值 = 客观表现(行动文本关键词命中)× 约束权重(制度重视度),
          专家调权重 → 反馈侧重变化 → 倾向滞后收敛(内化证据)。
        - 客观性保留在"测量方法"上(关键词启发式,不因人而异),
          不独立的是"目标集由制度定"。
        """
        text = (action_desc or "").lower()
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
        out = {}
        for goal, weight in constraints.items():
            if not weight or weight <= 0:
                continue
            kw = _GOAL_KEYWORDS.get(goal)
            if not kw:
                # 该目标无关键词定义 → 无法客观判定,不参与反馈
                continue
            pos, neg = kw
            pos_hit = sum(1 for k in pos if k in text)
            neg_hit = sum(1 for k in neg if k in text)
            if pos_hit or neg_hit:
                # 0.5 基准 + 正向/反向修正,截断到 [0,1] × 约束权重
                v = 0.5 + 0.25 * pos_hit - 0.25 * neg_hit
                v = max(0.0, min(1.0, v))
                out[goal] = v * weight
        return out
