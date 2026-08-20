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


# ---------------------------------------------------------------------------
# 投资场景默认角色(从 start.py 迁移)
# ---------------------------------------------------------------------------
personas = [
    "沈砚之",  # 首席投资顾问：价值投资派，负责资产配置与投资决策
    "苏清越",  # 量化交易分析师：数据驱动，负责交易模型与信号
    "陈慕白",  # 行业研究员：基本面分析，负责个股与行业调研
    "林晚晴",  # 风控合规专员：风险敏感，负责风险评估与止损设定
    "老周",    # 资深散户投资者：经验丰富但情绪化，易受市场情绪影响
]


# ---------------------------------------------------------------------------
# 模拟配置加载(从 start.py 迁移:新模拟 / 断点续跑)
# ---------------------------------------------------------------------------
import datetime


def load_config(start_time: str = "20240213-09:30", stride: int = 15,
                 agents: Optional[List[str]] = None) -> dict:
    """为新游戏创建配置(等价 start.get_config)"""
    with open("data/config.json", "r", encoding="utf-8") as f:
        json_data = json.load(f)
        agent_config = json_data["agent"]

    assets_root = os.path.join("assets", "village")
    config = {
        "stride": stride,
        "time": {"start": start_time},
        "maze": {"path": os.path.join(assets_root, "maze.json")},
        "agent_base": agent_config,
        "agents": {},
    }
    for a in (agents or []):
        config["agents"][a] = {
            "config_path": os.path.join(
                assets_root, "agents", a.replace(" ", "_"), "agent.json"
            ),
        }
    return config


def load_config_from_log(checkpoints_folder: str):
    """从存档数据中载入配置,用于断点恢复(等价 start.get_config_from_log)"""
    files = sorted(os.listdir(checkpoints_folder))

    json_files = list()
    for file_name in files:
        if file_name.endswith(".json") and file_name != "conversation.json":
            json_files.append(os.path.join(checkpoints_folder, file_name))

    if len(json_files) < 1:
        return None

    with open(json_files[-1], "r", encoding="utf-8") as f:
        config = json.load(f)

    assets_root = os.path.join("assets", "village")

    start_time = datetime.datetime.strptime(config["time"], "%Y%m%d-%H:%M")
    start_time += datetime.timedelta(minutes=config["stride"])
    config["time"] = {"start": start_time.strftime("%Y%m%d-%H:%M")}
    agents = config["agents"]
    for a in agents:
        config["agents"][a]["config_path"] = os.path.join(
            assets_root, "agents", a.replace(" ", "_"), "agent.json"
        )

    return config
