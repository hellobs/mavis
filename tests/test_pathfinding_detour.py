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
        self.name = "A"
        self._target_name = target_name

    def get_event(self):
        _name, _outer = self._target_name, self

        class _E:
            address = ["<persona>", _name]
        return _E()

    def get_tile(self):
        return self.maze.tile_at(self.coord)


for _m in ("find_path", "_target_center", "_outward_walkable",
           "_next_step_blocked", "_blocked_by_agents", "_mutual_approach"):
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


def test_cached_path_replans_when_next_step_occupied(maze):
    """路径的**下一格**被别人占住时,应重算绕行,而不是闷头沿旧路撞上去。

    这是 2026-10-09 用户第二次反馈的场景("一个人一直在走、被另外一个人挡住"):
    第一条修的是"目标格被占",这一条修的是"路**中途**被挡"。原因是 find_path
    开头 `if self.path: return self.path` 会让算好的路一直被复用 —— 别人站到路上
    后它照样沿旧路走,表现为原地卡住。
    """
    target = _open_area(maze, need=2)
    s = _Stub(maze, (target[0] - 4, target[1]))

    agents = {"B": _Other(target)}
    path1 = s.find_path(agents)
    assert path1, "先要有一条正常路径"
    # 模拟"正在沿这条路径走":把它设成缓存路径
    s.path = path1

    # 有人站到"原路径的下一格"上
    blocked = tuple(path1[0])
    agents["C"] = _Other(blocked)
    maze.tile_at(blocked).add_event(Event("C", address=["the Ville"]))

    path2 = s.find_path(agents)
    assert path2, "下一格被占时应重算出绕行路径(修复前会照样返回旧 path)"
    assert tuple(path2[0]) != blocked, \
        "重算后的下一格仍是被人占住的 %s —— 等于没绕" % (blocked,)


def test_mutual_approach_does_not_deadlock(maze):
    """两人互为对方目标、同步迈步时,不能对穿后僵死。

    2026-10-09 用户反馈"一个人一直在走、被另外一个人挡住"的可复现版:
    A 去 B 身边、B 去 A 身边,两人各自并行选"离自己最近的对方邻格",
    恰好互换位置(对穿) → 走完后两人相邻且互在对方身边 →
    `if tuple(self.coord) in target_tiles: return []` 直接返回空,
    **此后每一步都返回空** ⇒ 永久僵住,连交互也起不来。

    正确行为:即便贴住,也不能两人同时"永久原地"——至少要有通路让它们不再对穿
    (让一方先停/让路,另一方走到其身边的非重叠落点)。
    """
    # 找一条 4 格以上的竖直通道(两人分居两端,互为目标)；
    # 本仓 maze 最大开阔区只有 5×5,所以只能挑现成的直通道。
    corridor = _find_corridor(maze, length=5)
    (x, y0), y1 = corridor
    A = _Stub(maze, (x, y1), target_name="B")
    A.name = "A"
    B = _Stub(maze, (x, y0), target_name="A")
    B.name = "B"
    agents = {"A": A, "B": B}

    def _refresh():
        for c in [A.coord, B.coord]:
            t = maze.tile_at(c)
            t._events = {k: v for k, v in t._events.items()
                         if v.subject not in ("A", "B")}
        for nm, ag in agents.items():
            maze.tile_at(ag.coord).add_event(Event(nm, address=["the Ville"]))

    coords = []
    for _ in range(4):
        _refresh()
        pa, pb = A.find_path(agents), B.find_path(agents)
        coords.append((tuple(A.coord), tuple(B.coord)))
        if pa:
            A.coord = tuple(pa[-1])
        if pb:
            B.coord = tuple(pb[-1])

    # 判据:**两人任何一步都不能并到同一格**(修复前会并格、像连体一样一起走)。
    merged = [c for c in coords if c[0] == c[1]]
    assert not merged, "两人并到同一格(像连体一起走):%s" % (merged,)
    # 且至少要有一方动过(否则等于谁都不去)
    assert len({c for c in coords}) > 1, "两人都没有朝对方移动过:%s" % (coords,)


def _find_corridor(maze, length=5):
    """找一条**竖直连续可站**的通道,返回 ((x, y_top), y_bottom)。

    要求 length 格连续无碰撞,供"两人分居两端"用(本仓最大开阔区仅 5×5)。
    """
    for x in range(1, maze.maze_width - 1):
        run = 0
        for y in range(1, maze.maze_height - 1):
            if not maze.tile_at((x, y)).collision:
                run += 1
                if run >= length:
                    return (x, y - length + 1), y
            else:
                run = 0
    pytest.skip("maze 里找不到长度 %d 的连续通道" % length)

