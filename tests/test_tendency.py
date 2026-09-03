# -*- coding: utf-8 -*-
"""价值倾向惯性混合测试(IVD:人物底色 + 体验调制)

覆盖 mavisframework.core.agent_core.Agent 的倾向形成机制:
- 初始倾向 = agent.json 的 initial_tendency(人物底色)
- observe_consequence 后:倾向 = α×底色 + (1−α)×体验窗口,α 随体验渐降
- 起步=人设,体验累积后逐步向体验收敛,性格有残余(α 下限 0.1)
"""
import pytest

from mavisframework.core.agent_core import Agent


def _mk_agent(initial_tendency, window_size=15, decay=None):
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

    think_cfg = {"llm": {"provider": "mock"}, "tendency_window": window_size}
    if decay is not None:
        think_cfg["tendency_decay"] = decay
    cfg = {
        "name": "测试人",
        "currently": "x",
        "coord": [0, 0],
        "initial_tendency": initial_tendency,
        "percept": {"att_bandwidth": 4},
        "think": think_cfg,
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
        # 第一次体验:α = max(0.1, 1-1/15) ≈ 0.933 → 倾向 ≈ 底色(人设主导)
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

    def test_adaptive_decay_bound_is_window_size(self):
        # 自适应过渡期:α 衰减分母 = 记忆窗口容量(tendency_window),而非魔法数"8"
        # 窗口越小过渡越快:wal 容量 3 → 满 3 次体验后 α→0.1;容量 30 → 需更多体验
        small = _mk_agent({"Maximize Returns": 0.9, "Risk Aversion": 0.1}, window_size=3)
        large = _mk_agent({"Maximize Returns": 0.9, "Risk Aversion": 0.1}, window_size=30)
        small.attach_governance(None, None)
        large.attach_governance(None, None)
        small._consequence_fn = lambda self, d: {"Risk Aversion": 0.9, "Maximize Returns": 0.1}
        large._consequence_fn = lambda self, d: {"Risk Aversion": 0.9, "Maximize Returns": 0.1}
        # 各推 5 次体验:小窗口已装满窗口 → α 触底 0.1;大窗口 5/30 → α 仍高
        for _ in range(5):
            small.observe_consequence(f"s-{_}")
            large.observe_consequence(f"l-{_}")
        # 审计元信息记录 decay_total = 各自的窗口容量
        assert small.status["tendency_meta"]["decay_total"] == 3
        assert large.status["tendency_meta"]["decay_total"] == 30
        # 小窗口 α 触底更快 → 底色占更少 → 倾向更快转向体验
        small_alpha = small.status["tendency_meta"]["alpha"]
        large_alpha = large.status["tendency_meta"]["alpha"]
        assert small_alpha <= large_alpha
        # 小窗口几乎触底(5 obs ≥ 3 容量 → α=0.1)
        assert small_alpha == pytest.approx(0.1)
        # 大窗口仅 5/30 → α 仍明显高于 0.1
        assert large_alpha > 0.8

    def test_tendency_meta_reported_on_every_update(self):
        # 每次倾向更新都带 alpha/decay_total/obs,供"内化过渡程度"可解释
        agent = _mk_agent({"Maximize Returns": 0.8, "Risk Aversion": 0.2})
        agent.attach_governance(None, None)
        agent._consequence_fn = lambda self, d: {"Maximize Returns": 0.9}
        agent.observe_consequence("act once")
        m = agent.status.get("tendency_meta", {})
        assert m.get("obs") == 1
        assert m.get("decay_total") == 15  # 默认窗口容量
        assert 0.0 < m.get("alpha", 0.0) < 1.0

    def test_persistent_action_refreshes_periodically(self):
        # 同一行动持续时,每隔 tendency_refresh 步仍计入(持续强化,曲线不静止)
        agent = _mk_agent({"Maximize Returns": 0.5, "Risk Aversion": 0.5}, window_size=10)
        agent.think_config["tendency_refresh"] = 3
        agent.attach_governance(None, None)
        # 持续做同一件事,反馈一直偏 Maximize Returns
        agent._consequence_fn = lambda self, desc: {"Maximize Returns": 0.9, "Risk Aversion": 0.1}
        for i in range(10):
            agent.observe_consequence("do the same task")
        t = agent.value_tendency
        # 周期性刷新(3 步一次)使体验累积,MR 应显著高于起点 0.5
        assert t["Maximize Returns"] > 0.6
        assert agent._tendency_obs >= 3  # 至少刷新了 3 次(而非只 1 次)


class TestWindowDecay:
    """窗口指数衰减系数(think.tendency_decay):控制"干预后收敛速度"。

    旧 0.5:最近 1 条占 ~50%,干预后 1-3 条新反馈即主导窗口 → 0.5h 跳变;
    新默认 0.8:单条权重 ~20%,需多条新体验才主导 → 收敛渐进可见(内化滞后叙事)。
    """

    def _drive(self, agent, feedback_fn, rounds=10):
        agent.attach_governance(None, None)
        agent._consequence_fn = feedback_fn
        for i in range(rounds):
            agent.observe_consequence(f"act-{i}")

    def test_default_decay_is_08(self):
        agent = _mk_agent({"A": 0.5, "B": 0.5})
        assert agent._tendency_decay == pytest.approx(0.8)

    def test_decay_parses_from_config(self):
        agent = _mk_agent({"A": 0.5, "B": 0.5}, decay=0.95)
        assert agent._tendency_decay == pytest.approx(0.95)
        # 非法值回退默认
        bad = _mk_agent({"A": 0.5, "B": 0.5}, decay=1.5)
        assert bad._tendency_decay == pytest.approx(0.8)

    def test_lower_decay_converges_faster(self):
        # 干预后窗口被新反馈主导的速度:decay 越低单条新反馈权重越大 → 收敛越快
        # 构造:窗口已满(旧反馈 A 主导),灌入 1 条新反馈(B),比较窗口均值偏移
        def make(decay):
            a = _mk_agent({"A": 0.5, "B": 0.5}, window_size=15, decay=decay)
            a.attach_governance(None, None)
            # 旧体验:A 强反馈
            a._consequence_fn = lambda self, d: {"A": 0.9, "B": 0.1}
            for i in range(15):
                a.observe_consequence(f"old-{i}")
            # 干预后新体验:B 强反馈(仅 1 条)
            a._consequence_fn = lambda self, d: {"B": 0.9, "A": 0.1}
            a.observe_consequence("new-after-intervention")
            return a

        fast = make(0.5)   # 旧行为
        slow = make(0.8)   # 新默认
        # 仅 1 条新反馈后,decay 越小 B 占比越高(收敛越快)
        assert fast.value_tendency["B"] > slow.value_tendency["B"]
        # 0.8 下 1 条新反馈不应已主导(B 仍低于多数情况)——验证"渐进"而非"跳变"
        assert slow.value_tendency["B"] < 0.7

    def test_more_new_feedback_eventually_dominates(self):
        # 即便 decay=0.8,持续新反馈数条后 B 应主导(收敛最终完成,只是更渐进)
        agent = _mk_agent({"A": 0.5, "B": 0.5}, window_size=15, decay=0.8)
        agent.attach_governance(None, None)
        agent._consequence_fn = lambda self, d: {"A": 0.9, "B": 0.1}
        for i in range(15):
            agent.observe_consequence(f"old-{i}")
        agent._consequence_fn = lambda self, d: {"B": 0.9, "A": 0.1}
        for i in range(10):
            agent.observe_consequence(f"new-{i}")
        assert agent.value_tendency["B"] > agent.value_tendency["A"]
