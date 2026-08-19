"""framework.core.agent_core — Agent 生命周期(组件注入式,纯逻辑)

设计:Agent 通过"依赖注入"接收组件(LLM/记忆/空间/提示词),框架不绑定任何具体实现。
modules/agent.py 是当前业务实现;本模块是框架抽象(可插拔、可替换)。

Agent 每步(think)的编排:
    移动 → 取计划 → (睡/醒) → 感知 → 反应 → 反思 → 输出路径
"""
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class AgentCore(ABC):
    """Agent 抽象基类:定义生命周期接口"""

    @abstractmethod
    def think(self, status: Dict[str, Any], agents: Dict[str, "AgentCore"]) -> Dict[str, Any]:
        """单步思考,返回计划(含 path)"""
        ...

    @abstractmethod
    def percept(self) -> List[Any]:
        """感知周围,返回 concept 列表"""
        ...

    @abstractmethod
    def reflect(self) -> None:
        """反思(重要性累计超阈值)"""
        ...

    @abstractmethod
    def move(self, coord, path) -> List[Any]:
        """按路径移动,返回经过的事件"""
        ...

    @abstractmethod
    def find_path(self, agents: Dict[str, "AgentCore"]) -> List[List[int]]:
        """根据当前行动目标,算寻路路径"""
        ...

    @abstractmethod
    def is_awake(self) -> bool:
        ...

    def to_dict(self, with_action: bool = True) -> Dict[str, Any]:
        """序列化(存档/协议用)"""
        return {}


class SimpleAgent(AgentCore):
    """组件注入式 Agent:LLM/记忆/空间/提示词 均可插拔

    实际使用时,传入实现了相应接口的组件(现有 modules 或自定义)。
    """

    def __init__(
        self,
        name: str,
        llm=None,          # LLMProvider 接口
        memory=None,       # 记忆接口(retrieve/add)
        maze=None,         # 空间接口(find_path/get_scope/...)
        prompts=None,      # 提示词接口(prompt_xxx)
        config: Optional[dict] = None,
    ):
        self.name = name
        self.llm = llm
        self.memory = memory
        self.maze = maze
        self.prompts = prompts
        self.config = config or {}
        self.coord: Optional[List[int]] = None
        self.path: Optional[List[List[int]]] = None
        self.action = None
        self.concepts: List[Any] = []
        self.status: Dict[str, Any] = {"poignancy": 0}

    # ---- 生命周期 ----
    def think(self, status, agents):
        events = self.move(status.get("coord"), status.get("path"))
        plan, _ = self._current_plan()
        if plan and ("睡" in plan.get("describe", "") or plan.get("describe") == "sleeping") and self.is_awake():
            plan = self._go_sleep()
        if self.is_awake():
            self.percept()
            self._react(agents)
            self.reflect()
        self.plan = {
            "name": self.name,
            "path": self.find_path(agents),
            "emojis": {},
        }
        return self.plan

    def percept(self):
        if self.maze is None or self.coord is None:
            return []
        scope = self.maze.get_scope(self.coord, self.config.get("percept", {}))
        concepts = []
        for tile in scope:
            for e in tile.get_events():
                if e.subject != self.name:
                    concepts.append(e)
        self.concepts = concepts
        return concepts

    def reflect(self):
        threshold = self.config.get("think", {}).get("poignancy_max", 150)
        if self.status.get("poignancy", 0) < threshold:
            return
        self.status["poignancy"] = 0

    def move(self, coord, path):
        if coord is None:
            return []
        self.coord = list(coord)
        self.path = path
        return []

    def find_path(self, agents):
        if self.maze is None or self.coord is None:
            return []
        if self.action is None:
            return []
        target = self._action_target()
        if target is None:
            return []
        return self.maze.find_path(self.coord, target)

    def is_awake(self):
        if not self.action:
            return True
        desc = self._action_describe()
        return not (desc and "睡觉" in desc)

    # ---- 子类/组件需实现 ----
    def _current_plan(self):
        return None, None

    def _go_sleep(self):
        return {"describe": "正在睡觉", "path": []}

    def _react(self, agents):
        pass

    def _action_target(self):
        return None

    def _action_describe(self):
        return ""
