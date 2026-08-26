# -*- coding: utf-8 -*-
"""价值倾向惯性混合测试(IVD:人物底色 + 体验调制)

覆盖 mavisframework.core.agent_core.Agent 的倾向形成机制:
- 初始倾向 = agent.json 的 initial_tendency(人物底色)
- observe_consequence 后:倾向 = α×底色 + (1−α)×体验窗口,α 随体验渐降
- 起步=人设,体验累积后逐步向体验收敛,性格有残余(α 下限 0.1)
"""
import pytest

from mavisframework.core.agent_core import Agent


def _mk_agent(initial_tendency, window_size=15):
    """构造最小 Agent(注入假依赖,不触发 LLM)"""
    import mavisframework.core.agent_core as ac

    class _Tile:
        def __init__(self, coord=(0, 0)):
            self.coord = list(coord)
            self.events = []
            self.address = ["the Ville", "测试区"]
        def get_address(self, *a, **kw):
            if kw.get("as_list"):
                return list(self.address)
            return ":".join(self.address)
        def has_address(self, *a):
            return False
        def update_events(self, ev):
            return False
        def add_event(self, ev):
            self.events.append(ev)
        def remove_events(self, **kw):
            self.events = []
        def get_events(self):
            return list(self.events)
        def abstract(self):
            return {"address": self.address}

    class _Maze:
        def __init__(self):
            self._tiles = {}
        def tile_at(self, coord):
            key = tuple(coord)
            if key not in self._tiles:
                self._tiles[key] = _Tile(coord)
            return self._tiles[key]
        def get_scope(self, *a):
            return [self.tile_at((0, 0))]
        def get_around(self, *a):
            return [(0, 0)]
        def get_address_tiles(self, addr):
            return [(0, 0)]
        def update_obj(self, *a, **kw):
            return None

    class _Timer:
        def daily_duration(self, **kw):
            return 0
        def get_date(self, *a):
            return "20250213-09:30:00"

    cfg = {
        "name": "测试人",
        "currently": "x",
        "coord": [0, 0],
        "initial_tendency": initial_tendency,
        "percept": {"att_bandwidth": 4},
        "think": {"llm": {"provider": "mock"}, "tendency_window": window_size},
        "chat_iter": 0,
        "spatial": {"address": {}, "tree": {}},
        "schedule": {},
        "associate": {"embedding": {"provider": "simple"}},
        "scratch": {},
        "storage_root": "",
        "role_type": "user",
    }
    return Agent(cfg, _Maze(), {}, timer=_Timer())


class TestInitialTendency:
    def test_value_tendency_starts_with_persona(self):
        agent = _mk_agent({"Maximize Returns": 0.8, "Risk Aversion": 0.2})
        assert agent.value_tendency == {"Maximize Returns": 0.8, "Risk Aversion": 0.2}

    def test_no_initial_tendency_starts_empty(self):
        agent = _mk_agent({})
        assert agent.value_tendency == {}

    def test_inertia_blend_first_experience(self):
        # 第一次体验:α = max(0.1, 1-1/20) = 0.95 → 倾向 ≈ 底色(人设主导)
        agent = _mk_agent({"Maximize Returns": 0.8, "Risk Aversion": 0.2})
        agent.attach_governance(None, None)
        # 假后果反馈:只有 Maximize Returns 被命中(0.9)
        agent._consequence_fn = lambda self, desc: {"Maximize Returns": 0.9}
        agent.observe_consequence("buy more stock aggressively")
        t = agent.value_tendency
        # α=0.95:MR = 0.95*0.8 + 0.05*0.9 ≈ 0.805;RA = 0.95*0.2 ≈ 0.19 → 归一化后 MR 仍主导
        assert t["Maximize Returns"] > 0.7
        assert t["Risk Aversion"] < 0.3

    def test_inertia_decays_with_experience(self):
        # 多轮体验后 α → 0.1,倾向逐步向体验收敛,但保留 10% 底色
        agent = _mk_agent({"Maximize Returns": 0.9, "Risk Aversion": 0.1}, window_size=3)
        agent.attach_governance(None, None)
        # 20 轮都只反馈 Risk Aversion 高(与底色相反)
        agent._consequence_fn = lambda self, desc: {"Risk Aversion": 0.9, "Maximize Returns": 0.1}
        for i in range(20):
            agent.observe_consequence(f"action-{i}")
        t = agent.value_tendency
        # 体验足够多后 Risk Aversion 应占主导,但底色残余使 Maximize Returns 不归零
        assert t["Risk Aversion"] > t["Maximize Returns"]
        assert t["Maximize Returns"] > 0.02  # 性格残余
