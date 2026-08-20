"""framework.runtime.game — 游戏容器(创建/持有 agents + maze + conversation)

从 modules/game.py 迁移:去掉 modules 依赖,Agent 用 framework.core.agent_core.Agent,
时间用注入的 Timer。
"""
import copy
import os
from typing import Any, Dict, Optional

from framework.core.agent_core import Agent
from framework.core.timer import Timer
from framework.scene.maze import Maze


class Game:
    """The Game"""

    def __init__(self, name, static_root, config, conversation, timer=None, logger=None):
        self.name = name
        self.static_root = static_root
        self.record_iterval = config.get("record_iterval", 30)
        if logger is None:
            from framework.runtime.logger import get_logger

            logger = get_logger(f"game.{name}", level="info")
        self.logger = logger
        self._timer = timer or Timer()
        self.maze = Maze(self.load_static(config["maze"]["path"]), self.logger)
        self.conversation = conversation
        self.agents: Dict[str, Agent] = {}
        agent_base = config.get("agent_base", {})
        storage_root = os.path.join(f"results/checkpoints/{name}", "storage")
        if not os.path.isdir(storage_root):
            os.makedirs(storage_root)
        for name, agent in config["agents"].items():
            agent_config = self._update_dict(
                copy.deepcopy(agent_base), self.load_static(agent["config_path"])
            )
            agent_config = self._update_dict(agent_config, agent)

            agent_config["storage_root"] = os.path.join(storage_root, name)
            self.agents[name] = Agent(
                agent_config,
                self.maze,
                self.conversation,
                timer=self._timer,
                llm=None,
                logger=self.logger,
            )

    @staticmethod
    def _update_dict(base: dict, extra: dict) -> dict:
        base = {**base}
        for k, v in (extra or {}).items():
            if isinstance(v, dict) and isinstance(base.get(k), dict):
                base[k] = {**base[k], **v}
            else:
                base[k] = v
        return base

    def get_agent(self, name):
        return self.agents[name]

    def agent_think(self, name, status):
        agent = self.get_agent(name)
        plan = agent.think(status, self.agents)
        info = {
            "currently": agent.scratch.currently,
            "associate": agent.associate.abstract(),
            "concepts": {c.node_id: c.abstract() for c in agent.concepts},
            "chats": [
                {"name": "self" if n == agent.name else n, "chat": c}
                for n, c in agent.chats
            ],
            "action": agent.action.abstract(self._timer.get_date()),
            "schedule": agent.schedule.abstract(self._timer),
            "address": agent.get_tile().get_address(as_list=False),
        }
        if (
            self._timer.daily_duration() - agent.last_record
        ) > self.record_iterval:
            info["record"] = True
            agent.last_record = self._timer.daily_duration()
        else:
            info["record"] = False
        if agent.llm_available():
            info["llm"] = agent._llm.get_summary()
        title = "{}.summary @ {}".format(
            name, self._timer.get_date("%Y%m%d-%H:%M:%S")
        )
        self.logger.info("\n{}\n{}\n".format(split_line(title), agent))
        return {"plan": plan, "info": info}

    def load_static(self, path):
        import json

        with open(os.path.join(self.static_root, path), "r", encoding="utf-8") as f:
            return json.load(f)

    def reset_game(self):
        for a_name, agent in self.agents.items():
            agent.reset()
            title = "{}.reset".format(a_name)
            self.logger.info("\n{}\n{}\n".format(split_line(title), agent))


def split_line(title: str, fill: str = "=") -> str:
    width = max(len(title) + 4, 40)
    return title.center(width, fill)
