"""mavisframework.runtime.consequence — 客观后果反馈(IVD 重构)

为"AI 体验到什么行动成功"提供轻量的结果反馈:
- 根据行动文本与当前场景状态,判定该行动对各目标的"结果好坏"(0-1)
- 后果由"客观世界规则"决定,不偏袒任何目标(不因约束是风控就奖励风控)
- 反馈用于 Agent.observe_consequence → 更新 value_tendency(内化)

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

        启发式:行动若包含某目标的正向关键词,反馈高;反向关键词,反馈低。
        反馈独立于治理约束(不因约束偏好某目标)。
        """
        text = (action_desc or "").lower()
        if not text:
            return {}
        out = {}
        for goal, (pos, neg) in _GOAL_KEYWORDS.items():
            pos_hit = sum(1 for k in pos if k in text)
            neg_hit = sum(1 for k in neg if k in text)
            if pos_hit or neg_hit:
                # 0.5 基准 + 正向/反向修正,截断到 [0,1]
                v = 0.5 + 0.25 * pos_hit - 0.25 * neg_hit
                out[goal] = max(0.0, min(1.0, v))
        return out
