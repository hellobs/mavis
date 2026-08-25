"""framework.core.memory — 联想记忆与三因子检索(纯逻辑)

对应论文 4.1:检索分 = 近因性(0.995 指数衰减) + 重要性(poignancy) + 相关性(向量相似度),
min-max 归一化后加权求和。本模块只做"排序/加权"的纯逻辑,
向量检索由 storage 层提供(可插拔)。
"""
from typing import Any, Dict, List


def normalize(data: List[float], factor: float = 1.0, t_min: float = 0.0, t_max: float = 1.0) -> List[float]:
    """min-max 归一化到 [t_min, t_max],再乘 factor(等价原实现的归一化+加权)"""
    if not data:
        return []
    min_val, max_val = min(data), max(data)
    diff = max_val - min_val
    if diff == 0:
        return [(t_max - t_min) * factor / 2 for _ in data]
    return [(d - min_val) * (t_max - t_min) * factor / diff + t_min for d in data]


class RetrievalConfig:
    """三因子检索配置(默认与 config/associate 一致)"""

    def __init__(
        self,
        recency_decay: float = 0.995,
        recency_weight: float = 0.5,
        relevance_weight: float = 3.0,
        importance_weight: float = 2.0,
        retrieve_max: int = 30,
    ):
        self.recency_decay = recency_decay
        self.recency_weight = recency_weight
        self.relevance_weight = relevance_weight
        self.importance_weight = importance_weight
        self.retrieve_max = retrieve_max


class MemoryNode:
    """记忆节点(与存储层解耦的视图):文本 + 元数据"""

    def __init__(self, node_id: str, text: str, metadata: Dict[str, Any]):
        self.id = node_id
        self.text = text
        self.metadata = metadata  # 含 access(最近访问时间)、poignancy、score(相关性)


def rank_nodes(
    nodes: List[MemoryNode],
    config: RetrievalConfig,
    now_str: str = "",
) -> List[MemoryNode]:
    """三因子加权重排记忆节点(纯逻辑,对应 AssociateRetriever._retrieve 的核心)

    - 近因性:按 access 排序后,0.995^排名(越新越高)
    - 相关性:node.metadata['score'](由存储层提供,如向量余弦)
    - 重要性:node.metadata['poignancy']
    """
    if not nodes:
        return []

    # 按最近访问排序(为近因性)
    def _access(n: MemoryNode) -> str:
        return n.metadata.get("access", "")

    ordered = sorted(nodes, key=_access, reverse=True)

    fac = config.recency_decay
    recency = normalize([fac ** i for i in range(1, len(ordered) + 1)], config.recency_weight)
    relevance = normalize(
        [float(n.metadata.get("score", 0.0)) for n in ordered], config.relevance_weight
    )
    importance = normalize(
        [float(n.metadata.get("poignancy", 0.0)) for n in ordered], config.importance_weight
    )

    scored = [
        (n, r1 + r2 + i)
        for n, r1, r2, i in zip(ordered, recency, relevance, importance)
    ]
    scored.sort(key=lambda x: x[1], reverse=True)

    ranked = [n for n, _ in scored[: config.retrieve_max]]

    # 更新访问时间(影响下次近因性)
    if now_str:
        for n in ranked:
            n.metadata["access"] = now_str
    return ranked
