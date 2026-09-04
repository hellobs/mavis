# -*- coding: utf-8 -*-
"""价值倾向惯性混合测试(IVD:人物底色 + 体验调制)

覆盖 mavisframework.core.agent_core.Agent 的倾向形成机制:
- 初始倾向 = agent.json 的 initial_tendency(人物底色)
- observe_consequence 后:倾向 = α×底色 + (1−α)×体验窗口,α 随体验渐降
- 起步=人设,体验累积后逐步向体验收敛,性格有残余(α 下限 0.1)
"""
import datetime

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
        """可变模拟时钟:get_date() 返回 datetime,支持 forward(分钟)推进"""

        def __init__(self):
            self._t = datetime.datetime(2025, 2, 13, 9, 30)

        def daily_duration(self, **kw):
            return 0

        def get_date(self, *a):
            if a:
                return self._t.strftime(a[0])
            return self._t

        def forward(self, minutes):
            self._t += datetime.timedelta(minutes=minutes)

        def daily_time(self, duration):
            base = self._t.replace(hour=0, minute=0, second=0, microsecond=0)
            return base + datetime.timedelta(minutes=duration)

    think_cfg = {"llm": {"provider": "mock"}, "tendency_window": window_size}
    if decay is not None:
        think_cfg["tendency_decay_per_hour"] = decay
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
    """记忆近因性按"模拟时间"衰减(think.tendency_decay_per_hour, 缺省 0.6/小时)。

    论文语义(Generative Agents recency):权重 = decay^age_hours,age = 当前模拟
    时间 − 条目记录时间。与采样频率解耦——干预后新反馈再多,只要模拟时间没走够,
    旧记忆仍有分量 → 收敛为小时级渐进(而非 0.5-2h 被密集新反馈翻新)。
    """

    def _fill_old(self, agent, rounds):
        """以 A 强反馈灌满窗口;每次 observe 推进 2 分钟模拟时间"""
        agent.attach_governance(None, None)
        agent._consequence_fn = lambda self, d: {"A": 0.9, "B": 0.1}
        for i in range(rounds):
            agent._timer.forward(2)  # 模拟 2 分钟一步
            agent.observe_consequence(f"old-{i}")

    def test_default_decay_per_hour_is_06(self):
        agent = _mk_agent({"A": 0.5, "B": 0.5})
        assert agent._tendency_decay_per_hour == pytest.approx(0.6)

    def test_decay_parses_from_config(self):
        agent = _mk_agent({"A": 0.5, "B": 0.5}, decay=0.9)
        assert agent._tendency_decay_per_hour == pytest.approx(0.9)
        # 非法值回退默认
        bad = _mk_agent({"A": 0.5, "B": 0.5}, decay=1.5)
        assert bad._tendency_decay_per_hour == pytest.approx(0.6)

    def test_one_new_feedback_after_hour_still_not_dominant(self):
        # 干预后仅推进 1 模拟小时、灌 1 条新反馈(B):旧记忆(0.6^1=0.6)仍有分量,
        # B 不应主导(对比早期"按条数"方案:1 条新即可大幅偏移)
        a = _mk_agent({"A": 0.5, "B": 0.5}, window_size=15)
        self._fill_old(a, 15)
        a._timer.forward(60)  # 干预后过 1 模拟小时
        a._consequence_fn = lambda self, d: {"B": 0.9, "A": 0.1}
        a.observe_consequence("new-after-intervention")
        t = a.value_tendency
        # A(旧记忆)仍占主导:收敛是渐进的
        assert t["A"] > 0.6, "1h 后仅 1 条新反馈不应让倾向翻向 B,实际 A={}".format(round(t["A"], 3))

    def test_convergence_takes_hours(self):
        # 干预后持续 B 反馈,但每步仅 2 分钟:3 模拟小时内 B 不应完全主导;
        # 推进足够时间(模拟 ~6h)后 B 才明显占优 → 收敛是小时级
        a = _mk_agent({"A": 0.5, "B": 0.5}, window_size=15)
        self._fill_old(a, 15)
        a._consequence_fn = lambda self, d: {"B": 0.9, "A": 0.1}
        # 3 模拟小时(90 步 × 2min),间隔采样(每 30 步观察一次)
        for i in range(90):
            a._timer.forward(2)
            if i % 30 == 29:
                a.observe_consequence(f"new-{i}")
        t3h = a.value_tendency["B"]
        assert t3h < 0.8, "3h 不应完全收敛到位,B={}".format(round(t3h, 3))
        # 再推 6h(共 ~9h),B 应接近主导
        for i in range(180):
            a._timer.forward(2)
            if i % 30 == 29:
                a.observe_consequence(f"new2-{i}")
        t9h = a.value_tendency["B"]
        assert t9h > t3h + 0.05, "时间推进后应继续向新约束收敛:3h={} 9h={}".format(
            round(t3h, 3), round(t9h, 3))

    def test_decay_lower_means_slower(self):
        # decay 越低(0.4)旧记忆衰减越快 → 同样时间跨度内收敛更快;
        # 验证衰减系数单调影响收敛速度
        def run(decay):
            a = _mk_agent({"A": 0.5, "B": 0.5}, window_size=15, decay=decay)
            self._fill_old(a, 15)
            a._consequence_fn = lambda self, d: {"B": 0.9, "A": 0.1}
            a._timer.forward(120)  # 干预后 2 模拟小时
            a.observe_consequence("post")
            return a.value_tendency["B"]

        b04 = run(0.4)  # 衰减快 → 旧记忆弱 → B 高
        b06 = run(0.6)
        b08 = run(0.8)  # 衰减慢 → 旧记忆强 → B 低
        assert b04 > b06 > b08, "decay 越低收敛越快:{:.3f} {:.3f} {:.3f}".format(b04, b06, b08)
