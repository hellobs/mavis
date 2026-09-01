"""mavisframework.runtime.goal_scorer — embedding 语义对齐打分

计算文本与目标描述之间的语义对齐度(cosine similarity),供后果反馈
(ConsequenceEngine)使用:行动相对优势 = sim_i / Σsim_j,再按约束权重加权。
不再承担"选择/校验/重生成"职责——约束不进提示词,不强制行为。

实现轻量:直接调 Ollama /api/embeddings(HTTP),不依赖 llama_index。
"""
import os
import math
import json
import time
import threading
import urllib.request
from typing import Dict, List, Optional

# 全局 embedding 并发限制:Ollama 单模型处理能力有限,
# 6 个 agent 并行调用会排队超时(30s)导致大批 None → 倾向曲线变平。
# 用信号量把同时进行的 embedding 请求限制到 2,超时放大到 60s + 重试。
_EMBED_SEM = threading.Semaphore(2)
_EMBED_MAX_RETRY = 3
_EMBED_TIMEOUT = 60

# V6:类级共享 embedding 缓存——agent 的 GoalScorer 与 ConsequenceEngine 的
# GoalScorer 是不同实例(agent._goal_scorer vs engine._scorer),若各自
# 实例缓存,同一 action/goal 文本会被 embed 两次(observe 里 consequence
# 算 feedback + goal_alignment 算对齐,2 倍调用)。类级缓存跨实例共享,
# 首次命中后不再重复网络请求。带上限防长期运行内存增长。
_EMBED_CLASS_CACHE: Dict[str, List[float]] = {}
_EMBED_CACHE_MAX = 2000
_EMBED_CACHE_LOCK = threading.Lock()


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
        """获取文本向量(类级共享缓存 + 并发限制 + 重试)"""
        # 先查类级缓存(跨实例共享,避免重复 embed)
        with _EMBED_CACHE_LOCK:
            if text in _EMBED_CLASS_CACHE:
                return _EMBED_CLASS_CACHE[text]
        if text in self._cache:
            return self._cache[text]
        vec = None
        with _EMBED_SEM:
            for attempt in range(_EMBED_MAX_RETRY):
                try:
                    req = urllib.request.Request(
                        self.base_url + "/api/embeddings",
                        data=json.dumps({"model": self.model, "prompt": text}).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                    )
                    with urllib.request.urlopen(req, timeout=_EMBED_TIMEOUT) as r:
                        vec = json.loads(r.read().decode("utf-8"))["embedding"]
                    break
                except Exception:
                    vec = None
                    if attempt < _EMBED_MAX_RETRY - 1:
                        time.sleep(2 * (attempt + 1))  # 退避重试
        if vec is not None:
            self._cache[text] = vec
            with _EMBED_CACHE_LOCK:
                # 简单上限:超限清空(行动文本多样,保留最近;避免无限增长)
                if len(_EMBED_CLASS_CACHE) >= _EMBED_CACHE_MAX:
                    _EMBED_CLASS_CACHE.clear()
                _EMBED_CLASS_CACHE[text] = vec
        return vec

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
