"""framework.config.validator — 配置校验(为 AI 生成配置兜底)

分层校验,逐层失败即停止:
  1) 语法层    : JSON 合法、必填字段存在、字段类型正确
  2) 地图一致性: coord 在地图范围内、spatial 地址存在于地图
  3) 角色交叉  : relationships/story 引用的角色在 agents 中存在、
                 story.time 在合理格式、importance 在 1-10

用法:
    from mavisframework.config.validator import validate_agents, validate_relationships, validate_story
    errors = validate_agents(agents_cfg, maze_cfg)   # 返回错误列表,空=通过
"""
from typing import Any, Dict, List, Optional, Tuple
import os


# ---------------------------------------------------------------------------
# 字段约束定义(agent.json / relationships.json / story.json)
# ---------------------------------------------------------------------------
AGENT_REQUIRED = ["name", "coord", "currently", "scratch", "spatial"]
SCRATCH_REQUIRED = ["age", "innate", "learned", "lifestyle", "daily_plan"]
SPATIAL_KEYS = ["address", "tree"]

RELATION_REQUIRED = ["agents", "type"]
RELATION_OPTIONAL = ["direction", "trigger", "frequency"]
RELATION_FREQ = {"high", "medium", "low"}

STORY_REQUIRED = ["id", "time", "event_type", "content"]
STORY_OPTIONAL = ["targets", "expected", "importance"]


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def _collect_addresses(maze_cfg: Optional[dict]) -> set:
    """从 maze.json 收集所有完整地址(含 world 前缀,与 agent.json spatial 对齐)"""
    addrs = set()
    if not maze_cfg:
        return addrs
    world = maze_cfg.get("world", "")
    for t in maze_cfg.get("tiles", []):
        a = t.get("address", [])
        if a:
            full = ([world] + list(a)) if world and a[0] != world else list(a)
            addrs.add(":".join(full))
    return addrs


def _is_in_map(coord: Any, maze_cfg: Optional[dict]) -> bool:
    if not maze_cfg or not isinstance(coord, list) or len(coord) != 2:
        return False
    w, h = maze_cfg.get("size", [0, 0])
    x, y = coord
    return 0 <= x < w and 0 <= y < h


def _collect_tree_addresses(tree: Any, prefix: List[str] = None) -> List[str]:
    """递归收集 spatial.tree 中的叶子地址"""
    prefix = prefix or []
    out = []
    if isinstance(tree, dict):
        for k, v in tree.items():
            out.extend(_collect_tree_addresses(v, prefix + [k]))
    elif isinstance(tree, list):
        for item in tree:
            out.append(":".join(prefix + [str(item)]))
    return out


# ---------------------------------------------------------------------------
# 各文件校验
# ---------------------------------------------------------------------------
def validate_agents(agents: Dict[str, dict], maze_cfg: Optional[dict] = None,
                    agent_dir: str = "") -> List[str]:
    """校验所有 agent.json"""
    errors: List[str] = []
    addrs = _collect_addresses(maze_cfg)

    for name, cfg in agents.items():
        prefix = f"[agent:{name}]"

        # 语法层:必填字段
        for field in AGENT_REQUIRED:
            if field not in cfg:
                errors.append(f"{prefix} 缺少必填字段 '{field}'")
                continue
        if "scratch" in cfg:
            for field in SCRATCH_REQUIRED:
                if field not in cfg["scratch"]:
                    errors.append(f"{prefix}.scratch 缺少 '{field}'")
        if "spatial" in cfg:
            for field in SPATIAL_KEYS:
                if field not in cfg["spatial"]:
                    errors.append(f"{prefix}.spatial 缺少 '{field}'")

        # 字段类型
        if "coord" in cfg and not isinstance(cfg["coord"], list):
            errors.append(f"{prefix}.coord 必须是数组 [x, y]")

        # 地图一致性
        if "coord" in cfg and maze_cfg:
            if not _is_in_map(cfg["coord"], maze_cfg):
                errors.append(
                    f"{prefix}.coord {cfg['coord']} 超出地图范围 {maze_cfg.get('size')}"
                )
        if "spatial" in cfg and addrs:
            tree_addrs = _collect_tree_addresses(cfg["spatial"].get("tree", {}))
            for addr in tree_addrs:
                # 前缀匹配:地址逐级缩短,存在任意一级即通过
                parts = addr.split(":")
                matched = any(
                    ":".join(parts[:i]) in addrs for i in range(1, len(parts) + 1)
                )
                if not matched:
                    errors.append(f"{prefix}.spatial 地址 '{addr}' 不存在于地图")

        # 名字与目录一致(agent_dir 传了才查)
        if agent_dir and "name" in cfg:
            expected_dir = os.path.join(agent_dir, cfg["name"])
            if not os.path.isdir(expected_dir):
                errors.append(f"{prefix} 角色目录 '{cfg['name']}' 不存在于 {agent_dir}")

    return errors


def validate_relationships(relationships: List[dict],
                           agent_names: set) -> List[str]:
    """校验 relationships.json:引用角色必须存在"""
    errors: List[str] = []
    for idx, rel in enumerate(relationships):
        prefix = f"[relationship:{idx}]"
        for field in RELATION_REQUIRED:
            if field not in rel:
                errors.append(f"{prefix} 缺少 '{field}'")
                continue
        if "agents" in rel and isinstance(rel["agents"], list):
            for a in rel["agents"]:
                if a not in agent_names:
                    errors.append(f"{prefix} 引用的角色 '{a}' 不在 agents 配置中")
        if "frequency" in rel and rel["frequency"] not in RELATION_FREQ:
            errors.append(
                f"{prefix}.frequency 必须是 high/medium/low,得到 '{rel['frequency']}'"
            )
    return errors


def validate_story(story: List[dict], agent_names: set) -> List[str]:
    """校验 story.json:字段完整、targets 角色存在、importance 范围"""
    errors: List[str] = []
    for idx, ev in enumerate(story):
        prefix = f"[story:{idx}]"
        for field in STORY_REQUIRED:
            if field not in ev:
                errors.append(f"{prefix} 缺少 '{field}'")
        if "time" in ev:
            import re

            if not re.match(r"^\d{2}:\d{2}$", str(ev["time"])):
                errors.append(f"{prefix}.time 格式应为 HH:MM,得到 '{ev['time']}'")
        targets = ev.get("targets", [])
        if targets and targets != ["all"] and isinstance(targets, list):
            for t in targets:
                if t not in agent_names:
                    errors.append(f"{prefix} targets 引用的角色 '{t}' 不在 agents 配置中")
        if "importance" in ev:
            imp = ev["importance"]
            if not isinstance(imp, int) or not (1 <= imp <= 10):
                errors.append(f"{prefix}.importance 应为 1-10 的整数,得到 {imp}")
    return errors


def validate_all(agents: Dict[str, dict], relationships: List[dict],
                 story: List[dict], maze_cfg: Optional[dict] = None,
                 agent_dir: str = "") -> List[str]:
    """一次校验全部,返回汇总错误列表"""
    errors: List[str] = []
    errors.extend(validate_agents(agents, maze_cfg, agent_dir))
    agent_names = set(agents.keys())
    errors.extend(validate_relationships(relationships, agent_names))
    errors.extend(validate_story(story, agent_names))
    return errors
