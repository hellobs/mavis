"""mavisframework.runtime.governance — 制度约束层(IVD 重构)

治理者(专家)设定的"期望目标权重"存放于 governance.json,
与 AI 本体(agent.json)分离。约束不注入 prompt——
仅定义客观后果反馈的对照基准(期望),由 GoalScorer 度量偏差。

结构:
    governance.json = {
        "roles": {
            "an_example_agent": {"Maximize Returns": 0.7, "Risk Aversion": 0.3},
            ...
        }
    }
"""
import json
import os
from typing import Dict, Optional


class Governance:
    """制度约束层:加载/查询/更新治理者设定的期望目标权重"""

    def __init__(self, path: Optional[str] = None):
        self.path = path
        self.data: Dict = {"roles": {}}
        if path and os.path.exists(path):
            self.load(path)

    # ------------------------------------------------------------------
    # 加载/保存
    # ------------------------------------------------------------------
    def load(self, path: str):
        with open(path, "r", encoding="utf-8") as f:
            self.data = json.load(f)
        self.path = path
        self.data.setdefault("roles", {})

    def save(self, path: Optional[str] = None):
        path = path or self.path
        if not path:
            raise ValueError("governance path 未指定")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    def get_constraints(self, agent_name: str) -> Dict[str, float]:
        """该角色的期望目标权重(约束);未配置返回空"""
        return dict(self.data.get("roles", {}).get(agent_name, {}) or {})

    def all_constraints(self) -> Dict[str, Dict[str, float]]:
        return dict(self.data.get("roles", {}))

    # ------------------------------------------------------------------
    # 更新(治理者干预)
    # ------------------------------------------------------------------
    def set_constraints(self, agent_name: str, goals: Dict[str, float]):
        """更新某角色的期望目标权重(专家通过 Goals 面板调用)"""
        self.data.setdefault("roles", {})[agent_name] = dict(goals)
        self.save()

    def has(self, agent_name: str) -> bool:
        return bool(self.data.get("roles", {}).get(agent_name))
