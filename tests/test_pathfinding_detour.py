# -*- coding: utf-8 -*-
"""寻路:目标点被占时的绕行(2026-10-09 用户指出"agent 变成障碍物后会卡住")。

背景
----
`Agent.find_path` 去"某人身边"时,原来只把**那个人紧邻的 4 格**当候选落点。
若这 4 格恰好都站着别人(`_ignore_target` 会滤掉有他人 event 的格子),
`target_tiles` 变空 → 直接 `return []` → 角色**原地不动**,
而且下一步仍是空 → **永久卡住**,表现为"别人挡在那儿就过不去了,不会绕"。

修法:目标格全被占时,以目标为中心**逐环外扩**找可站格(见 `_outward_walkable`),
再照旧用 BFS 从这些落点里挑最近可达的。

本文件用**真实 Maze**(读仓内 maze.json)验证跨格行为,不用替身迷宫 ——
替身迷宫验不出"环扩展是否越过墙/出界"这类真实约束。
"""
import io
import json
import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_MAZE_JSON = os.path.join(
    os.path.dirname(_HERE), "..", "provenance", "provenance", "frontend",
    "static", "assets", "village", "maze.json")
_MAZE_JSON = os.path.normpath(_MAZE_JSON)

pytestmark = pytest.mark.skipif(
    not os.path.isfile(_MAZE_JSON),
    reason="provenance 仓不在预期位置(../provenance/...);跨仓集成验证归 provenance CI")

from mavisframework.core.agent_core import Agent          # noqa: E402
from mavisframework.core.event import Event               # noqa: E402
from mavisframework.scene.maze import Maze                # noqa: E402


class _Stub:
    """只挂 find_path 及其 helper 所需属性的最小替身。

    真 Agent 的构造要拉起 LLM/日程/记忆一堆东西,与"寻路几何"无关;
    这里只复现 find_path 读到的字段,并把真 Agent 的方法绑上来跑真代码。
    """

    def __init__(self, maze, coord, target_name="B"):
        self.coord = tuple(coord)
        self.path = []
        self.maze = maze
        self._target_name = target_name

    def get_event(self):
        _name, _outer = self._target_name, self

        class _E:
            address = ["<persona>", _name]
        return _E()

    def get_tile(self):
        return self.maze.tile_at(self.coord)


for _m in ("find_path", "_target_center", "_outward_walkable"):
    setattr(_Stub, _m, getattr(Agent, _m))


class _Other:
    def __init__(self, coord):
        self.coord = tuple(coord)


@pytest.fixture(scope="module")
def maze():
    with io.open(_MAZE_JSON, encoding="utf-8") as f:
        return Maze(json.load(f))


def _open_area(maze, need=2):
    """找一块以某格为中心、半径 need 内全部可通行的开阔区(远离墙带来的假阴性)。"""
    def walkable(x, y):
        if not (0 <= x < maze.maze_width and 0 <= y < maze.maze_height):
            return False
        return not maze.tile_at((x, y)).collision
    for y in range(need, maze.maze_height - need):
        for x in range(need, maze.maze_width - need):
            if all(walkable(x + dx, y + dy)
                   for dx in range(-need, need + 1)
                   for dy in range(-need, need + 1)):
                return (x, y)
    pytest.skip("maze 里找不到足够开阔的区域")


def test_target_surrounded_still_finds_detour(maze):
    """目标四周 4 格全被占 → 仍应给出绕行路径(修复前这里是空 = 永久卡住)。"""
    target = _open_area(maze)
    s = _Stub(maze, (target[0] - 3, target[1]))

    agents = {"B": _Other(target)}
    # 把目标紧邻 4 格全占上
    for i, (dx, dy) in enumerate([(-1, 0), (1, 0), (0, -1), (0, 1)]):
        n = "blocker%d" % i
        agents[n] = _Other((target[0] + dx, target[1] + dy))
        maze.tile_at(agents[n].coord).add_event(Event(n, address=["the Ville"]))

    # 前置断言:确认"紧邻 4 格"确实都不可用(否则这条测试不是在测目标场景)
    around = maze.get_around(target)
    usable = [c for c in around
              if not maze.tile_at(c).collision
              and not any(e.subject in agents for e in maze.tile_at(c).get_events())]
    assert usable == [], "夹具没造出'四周全占':still %s" % (usable,)

    path = s.find_path(agents)
    assert path, "目标四周全占时返回空 —— 角色会原地卡死(这正是要修的 bug)"
    end = path[-1]
    # 终点必须紧贴目标(第一圈可站格)——"绕行"不等于"随便走到天边"
    assert max(abs(end[0] - target[0]), abs(end[1] - target[1])) <= 2, \
        "落点离目标太远:%s(目标 %s)" % (end, target)


def test_far_target_ring_extends_past_occupied_inner(maze):
    """目标紧邻一圈被占 → 应在第 2 圈找到落点(验证'逐环外扩'真的会扩)。

    本仓 maze 最开阔处只有 5x5 且仅 6 处(need>=3 的无一处),所以只能占第 1 圈;
    这仍足够验"外扩":修复前第 1 圈全占即 return []。
    """
    target = _open_area(maze, need=2)
    s = _Stub(maze, (target[0] - 4, target[1]))

    agents = {"B": _Other(target)}
    occupied = []
    r = 1                                  # 占掉紧邻一圈所有可站格
    for dx in range(-r, r + 1):
        for dy in range(-r, r + 1):
            if max(abs(dx), abs(dy)) != r:
                continue
            c = (target[0] + dx, target[1] + dy)
            if maze.tile_at(c).collision:
                continue
            n = "occ_%d_%d" % (c[0], c[1])
            agents[n] = _Other(c)
            maze.tile_at(c).add_event(Event(n, address=["the Ville"]))
            occupied.append(c)
    assert occupied, "夹具没占上紧邻一圈"

    path = s.find_path(agents)
    assert path, "紧邻一圈全占时应外扩到第 2 圈找落点,而不是返回空"
    end = path[-1]
    assert 0 < max(abs(end[0] - target[0]), abs(end[1] - target[1])) <= 3, \
        "落点应在目标近旁的外圈,实得 %s(目标 %s)" % (end, target)


def test_no_agents_still_goes_to_adjacent_cell(maze):
    """无人占位时行为不变:落点仍是目标紧邻格(不因修复而退化到远圈)。"""
    target = _open_area(maze)
    s = _Stub(maze, (target[0] - 3, target[1]))
    path = s.find_path({"B": _Other(target)})
    assert path, "无人占位时应能找到路径"
    end = path[-1]
    assert max(abs(end[0] - target[0]), abs(end[1] - target[1])) == 1, \
        "无人占位时落点应紧邻目标,实得 %s" % (end,)
