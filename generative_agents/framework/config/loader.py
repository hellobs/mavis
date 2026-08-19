"""framework.config.loader — 从业务层(scenarios/)加载配置

统一加载:角色(agent.json)、场景(maze.json)、关系(relationships.json)、剧情(story.json)。
换业务 = 换 scenarios/ 目录,框架层零改动。
"""
import json
import os
from typing import Any, Dict, List, Optional


def load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


class ScenarioConfig:
    """一个业务场景的完整配置(加载自 scenarios/<name>/)"""

    def __init__(self, scenario_dir: str):
        self.dir = scenario_dir
        self.agents_dir = os.path.join(scenario_dir, "agents")
        self.scene_dir = os.path.join(scenario_dir, "scene")
        self.agents: Dict[str, dict] = {}          # name -> agent.json 内容
        self.maze: Optional[dict] = None           # maze.json 内容
        self.relationships: List[dict] = []        # relationships.json(可为空)
        self.story: List[dict] = []                # story.json(可为空)
        self.roles: Dict[str, str] = {}            # 角色名 -> 职位(决策导出用)
        self._load()

    def _load(self):
        # 1) 角色
        if os.path.isdir(self.agents_dir):
            for name in os.listdir(self.agents_dir):
                p = os.path.join(self.agents_dir, name, "agent.json")
                if os.path.exists(p):
                    cfg = load_json(p)
                    self.agents[cfg["name"]] = cfg
                    self.roles[cfg["name"]] = cfg.get("role", "")

        # 2) 场景
        maze_path = os.path.join(self.scene_dir, "maze.json")
        if os.path.exists(maze_path):
            self.maze = load_json(maze_path)

        # 3) 关系
        rel_path = os.path.join(scenario_dir_path(self.dir), "relationships.json")
        if os.path.exists(rel_path):
            data = load_json(rel_path)
            self.relationships = data.get("relations", [])

        # 4) 剧情
        story_path = os.path.join(scenario_dir_path(self.dir), "story.json")
        if os.path.exists(story_path):
            data = load_json(story_path)
            self.story = data.get("events", [])


def scenario_dir_path(scenario_dir: str) -> str:
    """返回 scenario_dir 本身(兼容传入的路径)"""
    return scenario_dir


def load_scenario(scenario_dir: str) -> ScenarioConfig:
    return ScenarioConfig(scenario_dir)
