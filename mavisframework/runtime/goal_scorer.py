"""mavisframework.runtime.goal_scorer — 目标权重决策级打分(硬约束)

把 agent 的目标权重(goals,总和=1)真正参与决策计算:
LLM 生成候选行动/日程后,用 embedding 计算行动文本与各目标的语义对齐度,
加权得分 score = Σ w_i * sim(action, goal_i),供上层做选择/校验/重生成。

实现轻量:直接调 Ollama /api/embeddings(HTTP),不依赖 llama_index。
"""
import os
import math
import json
import urllib.request
from typing import Dict, List, Optional


class GoalScorer:
    """基于 embedding 的目标一致性打分器"""

    def __init__(self, base_url: str = "http://127.0.0.1:11434",
                 model: str = "qwen3-embedding:0.6b-q8_0"):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._cache: Dict[str, List[float]] = {}

    # ------------------------------------------------------------------
    # embedding
    # ------------------------------------------------------------------
    def embed(self, text: str) -> Optional[List[float]]:
        """获取文本向量(带缓存)"""
        if text in self._cache:
            return self._cache[text]
        try:
            req = urllib.request.Request(
                self.base_url + "/api/embeddings",
                data=json.dumps({"model": self.model, "prompt": text}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=30) as r:
                vec = json.loads(r.read().decode("utf-8"))["embedding"]
            self._cache[text] = vec
            return vec
        except Exception:
            return None

    @staticmethod
    def _cosine(a: List[float], b: List[float]) -> float:
        """余弦相似度 [-1, 1]"""
        if not a or not b or len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(y * y for y in b))
        if na == 0 or nb == 0:
            return 0.0
        return dot / (na * nb)

    # ------------------------------------------------------------------
    # 打分
    # ------------------------------------------------------------------
    def score(self, action: str, goals: Dict[str, float]) -> float:
        """加权目标得分 = Σ w_i * sim(action, goal_i)

        返回 [0, 1];embedding 不可用时返回 None(调用方降级为纯 prompt 级)。
        """
        if not action or not goals:
            return None
        a_vec = self.embed(action)
        if a_vec is None:
            return None
        total = 0.0
        for goal, weight in goals.items():
            g_vec = self.embed(goal)
            if g_vec is None:
                continue
            sim = max(0.0, self._cosine(a_vec, g_vec))  # 截断到 [0,1]
            total += float(weight) * sim
        return total

    def alignment(self, action: str, goals: Dict[str, float]) -> Dict[str, float]:
        """逐目标对齐度(调试/展示用): {goal: sim}"""
        out = {}
        a_vec = self.embed(action)
        if a_vec is None:
            return out
        for goal in goals:
            g_vec = self.embed(goal)
            if g_vec is not None:
                out[goal] = round(max(0.0, self._cosine(a_vec, g_vec)), 4)
        return out
