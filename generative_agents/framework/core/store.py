"""framework.core.store — 记忆存储抽象(可插拔)

框架核心只依赖 MemoryStore 接口,不绑定任何具体存储:
- SimpleStore     :纯标准库(内存 dict + JSON 文件持久化),检索用词重叠近似相关性 —— 零第三方依赖,保证框架独立可跑
- LlamaIndexStore:可选增强,包装 llama_index 向量检索(与原 modules/storage 行为一致),懒加载
"""
import json
import os
import re
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class MemoryStore(ABC):
    """记忆存储接口:节点 = {text, metadata}"""

    @abstractmethod
    def add_node(self, text: str, metadata: Dict[str, Any], node_id: str = None):
        ...

    @abstractmethod
    def find_node(self, node_id: str) -> Any:
        ...

    @abstractmethod
    def get_nodes(self, filter=None) -> List[Any]:
        ...

    @abstractmethod
    def remove_nodes(self, node_ids: List[str], delete_from_docstore: bool = True):
        ...

    @abstractmethod
    def cleanup(self, now_str: str) -> List[str]:
        ...

    @abstractmethod
    def retrieve(self, text: str, node_ids: Optional[List[str]] = None, top_k: int = 5) -> List[Any]:
        ...

    @abstractmethod
    def save(self, path: Optional[str] = None):
        ...

    @property
    @abstractmethod
    def nodes_num(self) -> int:
        ...


# ---------------------------------------------------------------------------
# SimpleStore:纯标准库实现(零第三方依赖)
# ---------------------------------------------------------------------------
class _SimpleNode:
    """简单节点:与 llama_index TextNode 的读取接口对齐(text/id_/metadata/score)"""

    def __init__(self, text: str, node_id: str, metadata: Dict[str, Any], score: float = 0.0):
        self.text = text
        self.id_ = node_id
        self.metadata = metadata
        self.score = score

    def __repr__(self):
        return f"<SimpleNode {self.id_}>"


def _text_overlap(query: str, text: str) -> float:
    """词重叠相似度(近似相关性):query 与 text 共现的关键词比例"""
    if not query or not text:
        return 0.0
    q_words = set(re.findall(r"[\u4e00-\u9fff]|[A-Za-z0-9]+", query))
    t_words = set(re.findall(r"[\u4e00-\u9fff]|[A-Za-z0-9]+", text))
    if not q_words:
        return 0.0
    return len(q_words & t_words) / len(q_words)


class SimpleStore(MemoryStore):
    """纯标准库记忆存储:内存 dict + JSON 文件持久化

    检索:词重叠得分 + 近因(access 新→旧) + 重要性(poignancy) 三因子,
    与原三因子检索思路一致(相关性退化为词重叠,无向量模型也可用)。
    """

    def __init__(self, path: Optional[str] = None, max_nodes: int = 0):
        self._path = path
        self._max_nodes = max_nodes
        self._nodes: Dict[str, _SimpleNode] = {}
        self._counter = 0
        if path and os.path.exists(os.path.join(path, "simple_store.json")):
            self._load()

    def _next_id(self) -> str:
        node_id = "node_" + str(self._counter)
        self._counter += 1
        return node_id

    def add_node(self, text: str, metadata: Dict[str, Any], node_id: str = None):
        node_id = node_id or self._next_id()
        node = _SimpleNode(text, node_id, dict(metadata))
        self._nodes[node_id] = node
        if self._max_nodes > 0 and len(self._nodes) > self._max_nodes:
            # 淘汰最旧(按 create 排序)
            ordered = sorted(
                self._nodes.values(), key=lambda n: n.metadata.get("create", "")
            )
            for old in ordered[: len(self._nodes) - self._max_nodes]:
                self._nodes.pop(old.id_, None)
        return node

    def find_node(self, node_id: str) -> _SimpleNode:
        return self._nodes[node_id]

    def get_nodes(self, filter=None) -> List[_SimpleNode]:
        def _check(node):
            if not filter:
                return True
            return filter(node)

        return [n for n in self._nodes.values() if _check(n)]

    def remove_nodes(self, node_ids: List[str], delete_from_docstore: bool = True):
        for nid in node_ids:
            self._nodes.pop(nid, None)

    def cleanup(self, now_str: str) -> List[str]:
        import datetime

        now = datetime.datetime.strptime(now_str, "%Y%m%d-%H:%M:%S")
        remove_ids = []
        for node_id, node in self._nodes.items():
            try:
                create = datetime.datetime.strptime(
                    node.metadata.get("create", now_str), "%Y%m%d-%H:%M:%S"
                )
                expire = datetime.datetime.strptime(
                    node.metadata.get("expire", now_str), "%Y%m%d-%H:%M:%S"
                )
            except ValueError:
                continue
            if create > now or expire < now:
                remove_ids.append(node_id)
        self.remove_nodes(remove_ids)
        return remove_ids

    def retrieve(self, text: str, node_ids: Optional[List[str]] = None, top_k: int = 5, now_str: str = "") -> List[_SimpleNode]:
        nodes = [self._nodes[n] for n in node_ids] if node_ids else list(self._nodes.values())
        scored = []
        for n in nodes:
            relevance = _text_overlap(text, n.text)
            poignancy = float(n.metadata.get("poignancy", 0.0))
            access = n.metadata.get("access", "")
            scored.append((n, relevance, poignancy, access))
        # 先按相关性 + 重要性排序,同分按最近访问
        scored.sort(key=lambda x: (x[1] + x[2] * 0.1, x[3]), reverse=True)
        ranked = [n for n, *_ in scored[:top_k]]
        if now_str:
            for n in ranked:
                n.metadata["access"] = now_str
        return ranked

    def save(self, path: Optional[str] = None):
        path = path or self._path
        if not path:
            return
        os.makedirs(path, exist_ok=True)
        payload = {
            "counter": self._counter,
            "nodes": [
                {"text": n.text, "id": n.id_, "metadata": n.metadata}
                for n in self._nodes.values()
            ],
        }
        with open(os.path.join(path, "simple_store.json"), "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    def _load(self):
        with open(os.path.join(self._path, "simple_store.json"), "r", encoding="utf-8") as f:
            payload = json.load(f)
        self._counter = payload.get("counter", 0)
        for item in payload.get("nodes", []):
            self._nodes[item["id"]] = _SimpleNode(
                item["text"], item["id"], item["metadata"]
            )

    @property
    def nodes_num(self) -> int:
        return len(self._nodes)


# ---------------------------------------------------------------------------
# LlamaIndexStore:可选增强(向量检索,懒加载 llama_index)
# ---------------------------------------------------------------------------
class LlamaIndexStore(MemoryStore):
    """包装 llama_index 向量检索(与原 modules/storage/index.py 行为一致)

    仅在显式选择 embedding provider 时使用;import llama_index 失败则不可用。
    """

    def __init__(self, embedding_config: Dict[str, Any], path: Optional[str] = None):
        self._config = {"max_nodes": 0}
        from llama_index.embeddings.huggingface import HuggingFaceEmbedding
        from llama_index.embeddings.ollama import OllamaEmbedding
        from llama_index.embeddings.openai import OpenAIEmbedding
        from llama_index.core import Settings
        from llama_index.core.node_parser import SentenceSplitter

        provider = embedding_config["provider"]
        if provider == "hugging_face":
            embed_model = HuggingFaceEmbedding(model_name=embedding_config["model"])
        elif provider == "ollama":
            embed_model = OllamaEmbedding(
                model_name=embedding_config["model"],
                base_url=embedding_config["base_url"],
                ollama_additional_kwargs={"mirostat": 0},
            )
        elif provider == "openai":
            embed_model = OpenAIEmbedding(
                model_name=embedding_config["model"],
                api_base=embedding_config["base_url"],
                api_key=embedding_config["api_key"],
            )
        else:
            raise NotImplementedError(
                "embedding provider {} is not supported".format(provider)
            )

        Settings.embed_model = embed_model
        Settings.node_parser = SentenceSplitter(chunk_size=512, chunk_overlap=64)
        Settings.num_output = 1024
        Settings.context_window = 4096

        import llama_index.core as index_core

        if path and os.path.exists(path):
            self._index = index_core.load_index_from_storage(
                index_core.StorageContext.from_defaults(persist_dir=path),
                show_progress=True,
            )
            cfg_path = os.path.join(path, "index_config.json")
            if os.path.exists(cfg_path):
                with open(cfg_path, "r", encoding="utf-8") as f:
                    self._config = json.load(f)
        else:
            self._index = index_core.VectorStoreIndex([], show_progress=True)
        self._path = path

    def add_node(self, text: str, metadata: Dict[str, Any], node_id: str = None):
        import time

        from llama_index.core.schema import TextNode

        for _ in range(10):
            try:
                exclude_llm_keys = list(metadata.keys())
                exclude_embedding_keys = list(metadata.keys())
                node_id = node_id or "node_" + str(self._config["max_nodes"])
                self._config["max_nodes"] += 1
                node = TextNode(
                    text=text,
                    id_=node_id,
                    metadata=metadata,
                    excluded_llm_metadata_keys=exclude_llm_keys,
                    excluded_embed_metadata_keys=exclude_embedding_keys,
                )
                self._index.insert_nodes([node])
                return node
            except Exception as e:
                from framework.runtime.logger import get_logger

                get_logger("store").warning(f"LlamaIndexStore.add_node() error: {e}")
                time.sleep(5)
        raise RuntimeError("LlamaIndexStore.add_node() failed after 10 retries")

    def find_node(self, node_id: str):
        return self._index.docstore.docs[node_id]

    def get_nodes(self, filter=None):
        def _check(node):
            if not filter:
                return True
            return filter(node)

        return [n for n in self._index.docstore.docs.values() if _check(n)]

    def remove_nodes(self, node_ids: List[str], delete_from_docstore: bool = True):
        self._index.delete_nodes(node_ids, delete_from_docstore=delete_from_docstore)

    def cleanup(self, now_str: str) -> List[str]:
        import datetime

        now = datetime.datetime.strptime(now_str, "%Y%m%d-%H:%M:%S")
        remove_ids = []
        for node_id, node in self._index.docstore.docs.items():
            create = datetime.datetime.strptime(
                node.metadata["create"], "%Y%m%d-%H:%M:%S"
            )
            expire = datetime.datetime.strptime(
                node.metadata["expire"], "%Y%m%d-%H:%M:%S"
            )
            if create > now or expire < now:
                remove_ids.append(node_id)
        self.remove_nodes(remove_ids)
        return remove_ids

    def retrieve(self, text: str, node_ids: Optional[List[str]] = None, top_k: int = 5, now_str: str = "") -> List[Any]:
        try:
            from llama_index.core.vector_store.retrievers import VectorIndexRetriever
        except ImportError:
            from llama_index.core.indices.vector_store.retrievers import VectorIndexRetriever

        try:
            nodes = VectorIndexRetriever(
                self._index,
                similarity_top_k=top_k,
                node_ids=node_ids,
            ).retrieve(text)
            if now_str:
                for n in nodes:
                    n.metadata["access"] = now_str
            return nodes
        except Exception:
            return []

    def save(self, path: Optional[str] = None):
        path = path or self._path
        if not path:
            return
        self._index.storage_context.persist(path)
        with open(os.path.join(path, "index_config.json"), "w", encoding="utf-8") as f:
            json.dump(self._config, f, ensure_ascii=False)

    @property
    def nodes_num(self) -> int:
        return len(self._index.docstore.docs)


def create_memory_store(embedding_config: Dict[str, Any], path: Optional[str] = None) -> MemoryStore:
    """工厂:provider == 'simple' 用纯 stdlib,其余用 llama_index"""
    if embedding_config.get("provider") == "simple":
        return SimpleStore(path=path)
    return LlamaIndexStore(embedding_config, path)


def now_str() -> str:
    import datetime

    return datetime.datetime.now().strftime("%Y%m%d-%H:%M:%S")
