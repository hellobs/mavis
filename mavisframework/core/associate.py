"""framework.core.associate — 联想记忆(Associate)(纯逻辑 + 可插拔存储)

从 modules/memory/associate.py 迁移:去掉 llama_index 直接依赖,
存储走 MemoryStore 接口(SimpleStore / LlamaIndexStore)。
- Concept:记忆节点视图(事件 + poignancy + create/expire/access)
- Associate:事件/对话/想法 三类记忆的增删查与三因子检索
"""
import datetime
from typing import Any, Dict, List, Optional, Union

from mavisframework.core.event import Event
from mavisframework.core.store import MemoryStore, create_memory_store, now_str
from mavisframework.core.timer import Timer, to_date


class Concept:
    def __init__(
        self,
        describe,
        node_id,
        node_type,
        subject,
        predicate,
        object,
        address,
        poignancy,
        create=None,
        expire=None,
        access=None,
        timer: Optional[Timer] = None,
    ):
        self.node_id = node_id
        self.node_type = node_type
        self.event = Event(
            subject, predicate, object, describe=describe, address=address.split(":")
        )
        self.poignancy = poignancy
        timer = timer or Timer()
        self.create = to_date(create) if create else timer.get_date()
        if expire:
            self.expire = to_date(expire)
        else:
            self.expire = self.create + datetime.timedelta(days=30)
        self.access = to_date(access) if access else self.create

    def abstract(self):
        return {
            "{}(P.{})".format(self.node_type, self.poignancy): str(self.event),
            "duration": "{} ~ {} (access: {})".format(
                self.create.strftime("%Y%m%d-%H:%M"),
                self.expire.strftime("%Y%m%d-%H:%M"),
                self.access.strftime("%Y%m%d-%H:%M"),
            ),
        }

    @property
    def describe(self):
        return self.event.get_describe()

    @classmethod
    def from_node(cls, node, timer: Optional[Timer] = None):
        return cls(
            node.text,
            node.id_,
            **node.metadata,
            timer=timer,
        )

    @classmethod
    def from_event(cls, node_id, node_type, event, poignancy, timer: Optional[Timer] = None):
        return cls(
            event.get_describe(),
            node_id,
            node_type,
            event.subject,
            event.predicate,
            event.object,
            ":".join(event.address),
            poignancy,
            timer=timer,
        )


class Associate:
    def __init__(
        self,
        path: str,
        embedding: Dict[str, Any],
        retention: int = 8,
        max_memory: int = -1,
        max_importance: int = 10,
        recency_decay: float = 0.995,
        recency_weight: float = 0.5,
        relevance_weight: float = 3,
        importance_weight: float = 2,
        memory: Optional[Dict[str, List[str]]] = None,
        timer: Optional[Timer] = None,
    ):
        self._timer = timer or Timer()
        self._index = create_memory_store(embedding, path)
        self.memory = memory or {"event": [], "thought": [], "chat": []}
        self.cleanup_index()
        self.retention = retention
        self.max_memory = max_memory
        self.max_importance = max_importance
        self._retrieve_config = {
            "recency_decay": recency_decay,
            "recency_weight": recency_weight,
            "relevance_weight": relevance_weight,
            "importance_weight": importance_weight,
        }

    def abstract(self):
        des = {"nodes": self._index.nodes_num}
        for t in ["event", "chat", "thought"]:
            des[t] = [self.find_concept(c).describe for c in self.memory[t]]
        return des

    def cleanup_index(self):
        now = self._timer.get_date("%Y%m%d-%H:%M:%S")
        node_ids = self._index.cleanup(now)
        self.memory = {
            n_type: [n for n in nodes if n not in node_ids]
            for n_type, nodes in self.memory.items()
        }

    def add_node(
        self,
        node_type: str,
        event: Event,
        poignancy: float,
        create=None,
        expire=None,
        filling=None,
    ):
        create = create or self._timer.get_date()
        expire = expire or (create + datetime.timedelta(days=30))
        metadata = {
            "node_type": node_type,
            "subject": event.subject,
            "predicate": event.predicate,
            "object": event.object,
            "address": ":".join(event.address),
            "poignancy": poignancy,
            "create": create.strftime("%Y%m%d-%H:%M:%S"),
            "expire": expire.strftime("%Y%m%d-%H:%M:%S"),
            "access": create.strftime("%Y%m%d-%H:%M:%S"),
        }
        node = self._index.add_node(event.get_describe(), metadata)
        memory = self.memory[node_type]
        memory.insert(0, node.id_)
        if len(memory) >= self.max_memory > 0:
            self._index.remove_nodes(memory[self.max_memory:])
            self.memory[node_type] = memory[: self.max_memory - 1]
        return self.to_concept(node)

    def to_concept(self, node):
        return Concept.from_node(node, timer=self._timer)

    def find_concept(self, node_id: str):
        return self.to_concept(self._index.find_node(node_id))

    def _retrieve_nodes(self, node_type: str, text: str = None):
        if text:
            nodes = self._index.retrieve(
                text, node_ids=self.memory[node_type], top_k=self.retention,
                now_str=self._timer.get_date("%Y%m%d-%H:%M:%S"),
            )
        else:
            nodes = [self._index.find_node(n) for n in self.memory[node_type]]
        return [self.to_concept(n) for n in nodes[: self.retention]]

    def retrieve_events(self, text: str = None):
        return self._retrieve_nodes("event", text)

    def retrieve_thoughts(self, text: str = None):
        return self._retrieve_nodes("thought", text)

    def retrieve_chats(self, name: str = None):
        text = ("对话 " + name) if name else None
        return self._retrieve_nodes("chat", text)

    def retrieve_focus(self, focus: List[str], retrieve_max: int = 30, reduce_all: bool = True):
        """多焦点检索:每个焦点文本取相关记忆,合并去重

        - reduce_all=True :合并为一个去重列表(事件+想法)
        - reduce_all=False:按焦点分组 {text: [concepts]}(反思用)
        """
        node_ids = self.memory["event"] + self.memory["thought"]
        retrieved: Dict[str, Any] = {}
        for text in focus:
            nodes = self._index.retrieve(
                text, node_ids=node_ids, top_k=retrieve_max,
                now_str=self._timer.get_date("%Y%m%d-%H:%M:%S"),
            )
            if reduce_all:
                retrieved.update({n.id_: n for n in nodes})
            else:
                retrieved[text] = nodes
        if reduce_all:
            return [self.to_concept(v) for v in retrieved.values()]
        return {text: [self.to_concept(n) for n in nodes] for text, nodes in retrieved.items()}

    def get_relation(self, node: Concept):
        return {
            "node": node,
            "events": self.retrieve_events(node.describe),
            "thoughts": self.retrieve_thoughts(node.describe),
        }

    def to_dict(self):
        self._index.save()
        return {"memory": self.memory}

    @property
    def index(self):
        return self._index
