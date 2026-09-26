# -*- coding: utf-8 -*-
"""IVD 核心链路回归测试(最近修复的 bug 不再复发)

覆盖 agent_core.Agent 的:
- 窗口内容持久化:status["tendency_window"] 含 行动/对齐度/反馈 三件套
- resume 恢复:窗口与体验计数从存档还原,α 不从头衰减
- 约束外目标剔除:制度删除的目标(如 Data Rigor)在倾向中消退至 0
- 干预同步:get_constraints 反映治理层最新约束
- 多轮体验收敛:倾向向新约束渐进收敛(不跳变)
"""
import pytest

from mavisframework.core.agent_core import Agent
from mavisframework.runtime.governance import Governance


class _Tile:
    def __init__(self, coord=(0, 0)):
        self.coord = list(coord)
        self.events = []
        self.address = ["the Ville", "测试区"]
        # agent_core 恢复路径会筛"可走格"(`tile.collision`);真实 Tile 有这个字段,
        # 这个假 Tile 原先漏了 → 测试在 AttributeError 上红(2026-09-21 补)
        self.collision = False
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


class _NullScorer:
    """密封测试用:goal_alignment 返回空 dict,与"Ollama 不可达"的回退路径等价。

    不注入时,observe_consequence 内部的 goal_alignment 会构造真 GoalScorer
    打 Ollama embedding —— Ollama 在线时套件 2s,离线时每个文本等死超时
    (2026-09-26 体检:整套件 641s,元凶就是这 4 个测试)。
    """

    def alignment(self, action, goals):
        return {}


def _mk_agent(initial_tendency, window_size=15, status=None):
    """构造最小 Agent(注入假依赖,不触发 LLM)"""
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
    if status:
        cfg["status"] = status
    agent = Agent(cfg, _Maze(), {}, timer=_Timer())
    agent._goal_scorer = _NullScorer()      # 密封:不真打 Ollama embedding
    return agent


def _attach(agent, constraints, feedback_fn):
    """挂治理约束 + 假后果反馈,返回 agent"""
    gov = Governance()
    gov.data = {"roles": {"测试人": dict(constraints)}}
    agent.attach_governance(gov, feedback_fn)
    return agent


class TestWindowPersistence:
    def test_window_records_action_alignment_feedback(self):
        # 窗口条目必须含 行动/对齐度/反馈 三件套(可解释性数据源)
        agent = _mk_agent({"A": 0.5, "B": 0.5}, window_size=5)
        _attach(agent, {"A": 0.6, "B": 0.4},
                lambda self, desc: {"A": 0.8, "B": 0.2})
        agent.observe_consequence("do task one")
        entry = agent.status["tendency_window"][0]
        assert "action" in entry
        assert "alignment" in entry
        assert "feedback" in entry
        assert "time" in entry  # 窗口明细按时间从早到晚展示所需
        assert entry["action"] == "do task one"
        assert entry["feedback"]["A"] == 0.8

    def test_window_bounded_by_size(self):
        agent = _mk_agent({"A": 0.5, "B": 0.5}, window_size=3)
        _attach(agent, {"A": 0.6, "B": 0.4},
                lambda self, desc: {"A": 0.8, "B": 0.2})
        for i in range(10):
            agent.observe_consequence(f"task-{i}")
        assert len(agent._tendency_window) == 3
        assert len(agent.status["tendency_window"]) == 3


class TestResumeRestore:
    def test_resume_restores_tendency_and_obs(self):
        # 模拟存档:value_tendency + 窗口内容 + 体验计数
        saved_status = {
            "value_tendency": {"A": 0.7, "B": 0.3},
            "tendency_window_n": 12,
            "tendency_window": [
                {"action": "old", "alignment": {"A": 0.6},
                 "feedback": {"A": 0.75, "B": 0.25}},
                {"action": "old2", "alignment": {"A": 0.6},
                 "feedback": {"A": 0.7, "B": 0.3}},
            ],
        }
        agent = _mk_agent({"A": 0.5, "B": 0.5}, window_size=15,
                          status=saved_status)
        # 恢复:倾向 = 存档值;体验计数 = 12 → α = max(0.1, 1-12/8) = 0.1
        assert agent.value_tendency == {"A": 0.7, "B": 0.3}
        assert agent._tendency_obs == 12
        assert len(agent._tendency_window) == 2

    def test_attach_governance_keeps_restored_obs(self):
        # attach_governance 不得清空已恢复的体验计数(否则 α 从头衰减)
        saved_status = {
            "value_tendency": {"A": 0.7, "B": 0.3},
            "tendency_window_n": 12,
        }
        agent = _mk_agent({"A": 0.5, "B": 0.5}, status=saved_status)
        gov = Governance()
        gov.data = {"roles": {"测试人": {"A": 0.6, "B": 0.4}}}
        agent.attach_governance(gov, lambda self, d: {"A": 0.8, "B": 0.2})
        assert agent._tendency_obs == 12  # 不被清零
        assert agent.value_tendency == {"A": 0.7, "B": 0.3}  # 不被覆盖

    def test_old_format_window_restored(self):
        # 兼容旧存档:窗口条目是裸 feedback dict(无 action/alignment)
        saved_status = {
            "value_tendency": {"A": 0.6, "B": 0.4},
            "tendency_window_n": 3,
            "tendency_window": [{"A": 0.8, "B": 0.2}],
        }
        agent = _mk_agent({"A": 0.5, "B": 0.5}, status=saved_status)
        assert len(agent._tendency_window) == 1
        assert agent._tendency_window[0]["feedback"] == {"A": 0.8, "B": 0.2}
        assert agent._tendency_window[0]["action"] == ""


class TestConstraintFiltering:
    def test_deleted_goal_removed_from_tendency(self):
        # 制度删除 Data Rigor 后,倾向中该目标应消退(不留底色残余)
        agent = _mk_agent({"A": 0.4, "B": 0.3, "C": 0.3}, window_size=5)
        # 约束只含 A、B(删除了 C)
        _attach(agent, {"A": 0.7, "B": 0.3},
                lambda self, desc: {"A": 0.8, "B": 0.2})
        for i in range(10):
            agent.observe_consequence(f"task-{i}")
        assert "C" not in agent.value_tendency

    def test_tendency_follows_new_constraints(self):
        # 干预后(约束权重变化),多轮体验后倾向朝新约束收敛
        agent = _mk_agent({"A": 0.5, "B": 0.5}, window_size=10)
        gov = Governance()
        gov.data = {"roles": {"测试人": {"A": 0.3, "B": 0.7}}}  # 新约束:B 主导
        agent.attach_governance(gov, lambda self, d: {"A": 0.2, "B": 0.8})
        for i in range(30):
            agent.observe_consequence(f"task-{i}")
        # 体验主导后 B 应显著高于 A(向约束 0.7 收敛)
        assert agent.value_tendency["B"] > agent.value_tendency["A"]
        assert agent.value_tendency["B"] > 0.55


class TestConstraintSync:
    def test_get_constraints_reflects_governance(self):
        # get_constraints 反映治理层(内存同步后)的最新约束
        agent = _mk_agent({"A": 0.5, "B": 0.5})
        gov = Governance()
        gov.data = {"roles": {"测试人": {"A": 0.6, "B": 0.4}}}
        agent.attach_governance(gov, None)
        assert agent.get_constraints() == {"A": 0.6, "B": 0.4}
        # 治理层更新(等价干预同步内存)
        gov.data["roles"]["测试人"] = {"A": 0.9, "B": 0.1}
        assert agent.get_constraints() == {"A": 0.9, "B": 0.1}


class TestV1ResumeLastAction:
    def test_resume_restores_last_window_action(self):
        # V1:resume 后 _last_window_action 应从窗口最后一条恢复,
        # 否则首步误判"行动变化"立即采样(破坏连续性)
        saved_status = {
            "value_tendency": {"A": 0.6, "B": 0.4},
            "tendency_window_n": 2,
            "tendency_window": [
                {"action": "old-task-1", "alignment": {"A": 0.6}, "feedback": {"A": 0.7, "B": 0.3}},
                {"action": "current-task", "alignment": {"A": 0.6}, "feedback": {"A": 0.7, "B": 0.3}},
            ],
        }
        agent = _mk_agent({"A": 0.5, "B": 0.5}, status=saved_status)
        assert agent._last_window_action == "current-task"

    def test_resume_same_action_not_immediately_sampled(self):
        # V1:resume 后若行动与最后一条相同,首步不应立即入窗(需等 refresh)
        saved_status = {
            "value_tendency": {"A": 0.6, "B": 0.4},
            "tendency_window_n": 2,
            "tendency_window": [
                {"action": "x", "alignment": {"A": 0.6}, "feedback": {"A": 0.7, "B": 0.3}},
            ],
        }
        agent = _mk_agent({"A": 0.5, "B": 0.5}, status=saved_status)
        gov = Governance()
        gov.data = {"roles": {"测试人": {"A": 0.6, "B": 0.4}}}
        agent.attach_governance(gov, lambda self, d: {"A": 0.7, "B": 0.3})
        agent.think_config["tendency_refresh"] = 5
        obs_before = agent._tendency_obs
        agent.observe_consequence("x")  # 与最后一条相同 → 不应立即采样
        assert agent._tendency_obs == obs_before  # 体验数不变


class TestNoSleepCleanup:
    """no_sleep 角色:旧存档残留的"空闲待命"(日程段 + 恢复的 action)在 Agent 构造时清洗。

    2026-09-21 去业务化:替换文案不再是内核字面量 ——
    - `config["idle_text"]` 决定**写什么**(默认通用英文,场景可覆盖);
    - `config["idle_text_map"]` 决定**要清洗哪些旧片段**(缺省空 = 不改写,内核零案例措辞)。
    场景(如 case01)把中文旧片段写进 map,行为与之前一致;内核自己不认识这些词。
    """

    # 案例侧会提供的清洗表(测试里由 helper 注入,模拟场景配置)
    IDLE_MAP = {"空闲待命": "Idle standby, staying online, no user inquiries",
                "无用户咨询": "Idle standby, staying online, no user inquiries"}

    def _mk_no_sleep_agent(self, with_action=None, daily_schedule=None,
                           idle_text_map=None, idle_text=None):
        cfg = {
            "name": "测试人",
            "currently": "x",
            "coord": [0, 0],
            "initial_tendency": {"A": 0.5, "B": 0.5},
            "percept": {"att_bandwidth": 4},
            "think": {"llm": {"provider": "mock"}, "tendency_window": 5},
            "chat_iter": 0,
            "spatial": {"address": {}, "tree": {}},
            "schedule": {"daily_schedule": daily_schedule or []},
            "associate": {"embedding": {"provider": "simple"}},
            "scratch": {},
            "storage_root": "",
            "role_type": "user",
            "no_sleep": True,
            # 案例侧提供清洗表(默认用类属性,便于"不给表"的对照测试传 {})
            "idle_text_map": self.IDLE_MAP if idle_text_map is None else idle_text_map,
        }
        if idle_text:
            cfg["idle_text"] = idle_text
        if with_action:
            cfg["action"] = with_action
        agent = Agent(cfg, _Maze(), {}, timer=_Timer())
        agent._goal_scorer = _NullScorer()   # 密封:不真打 Ollama embedding
        return agent

    def test_restored_action_chinese_idle_cleaned(self):
        # resume 恢复的 action(存档里执行中的段)若为中文"空闲待命"应清洗为英文
        action_cfg = {
            "event": {
                "subject": "测试人", "predicate": "此时",
                "object": "空闲待命,保持在线,无用户咨询",
                "describe": "空闲待命,保持在线,无用户咨询",
                "address": ["the Ville"], "emoji": "空闲待命,保持在线,无用户咨询",
            },
            "obj_event": {
                "subject": "Corridor", "predicate": "此时",
                "object": "idle and waiting", "describe": "idle and waiting",
                "address": ["the Ville"], "emoji": "",
            },
            "start": "20250213-00:00:00",
            "duration": 60,
        }
        agent = self._mk_no_sleep_agent(with_action=action_cfg)
        assert agent.action.event.object == "Idle standby, staying online, no user inquiries"
        assert agent.action.event.emoji == "Idle standby, staying online, no user inquiries"
        # obj_event 是英文,不应被误改
        assert agent.action.obj_event.object == "idle and waiting"

    def test_schedule_chinese_idle_cleaned(self):
        # daily_schedule 中的中文"空闲待命"段在构造时清洗(替换文案来自 idle_text/默认值)
        agent = self._mk_no_sleep_agent(daily_schedule=[
            {"describe": "空闲待命,保持在线,无用户咨询", "decompose": []},
            {"describe": "Go to sleep at 23:00", "decompose": []},
            {"describe": "Walks to the market news station", "decompose": []},
        ])
        descs = [str(p.get("describe", "")) for p in agent.schedule.daily_schedule]
        assert descs[0] == "Idle standby, staying online, no user inquiries"  # 中文待命→英文
        assert descs[1] == "Idle standby, staying online, no user inquiries"  # sleep 段→英文
        assert descs[2] == "Walks to the market news station"                 # 正常英文不动

    def test_no_map_means_no_rewrite_core_has_no_case_words(self):
        """**内核零案例措辞**的回归:不给 `idle_text_map` 时,内核一个字都不改。

        (以前内核写死了"空闲待命/无用户咨询"这两个具体片段 —— 那是案例词汇长在框架里。
         现在清洗表由场景提供;内核只认表,不认识词。)
        """
        agent = self._mk_no_sleep_agent(idle_text_map={}, daily_schedule=[
            {"describe": "空闲待命,保持在线,无用户咨询", "decompose": []},
        ])
        assert agent.schedule.daily_schedule[0]["describe"] == "空闲待命,保持在线,无用户咨询"

    def test_idle_text_is_overridable_by_scenario(self):
        """写什么由场景定:config["idle_text"] 覆盖默认文案(要中文也行)。"""
        agent = self._mk_no_sleep_agent(idle_text="待命中(全天在线)")
        assert agent.idle_text == "待命中(全天在线)"

    def test_user_without_no_sleep_not_cleaned(self):
        # 非 no_sleep 角色(user 但未配置)不应被清洗(保持人设作息)
        cfg = {
            "name": "测试人", "currently": "x", "coord": [0, 0],
            "initial_tendency": {"A": 0.5, "B": 0.5},
            "percept": {"att_bandwidth": 4},
            "think": {"llm": {"provider": "mock"}, "tendency_window": 5},
            "chat_iter": 0,
            "spatial": {"address": {}, "tree": {}},
            "schedule": {"daily_schedule": [{"describe": "空闲待命,保持在线,无用户咨询", "decompose": []}]},
            "associate": {"embedding": {"provider": "simple"}},
            "scratch": {}, "storage_root": "", "role_type": "user",
        }
        agent = Agent(cfg, _Maze(), {}, timer=_Timer())
        assert agent.schedule.daily_schedule[0]["describe"] == "空闲待命,保持在线,无用户咨询"


class TestV2WindowConstraintFiltering:
    def test_deleted_goal_removed_from_window(self):
        # V2:约束删除目标后,窗口内该目标的 feedback/alignment 也应清理,
        # 不留"影子目标"(滑出前仍影响加权平均)
        agent = _mk_agent({"A": 0.4, "B": 0.3, "C": 0.3}, window_size=5)
        # 先带 C 观察(窗口里留下 C 的反馈)
        gov = Governance()
        gov.data = {"roles": {"测试人": {"A": 0.4, "B": 0.3, "C": 0.3}}}
        agent.attach_governance(gov, lambda self, d: {"A": 0.5, "B": 0.3, "C": 0.2})
        agent.observe_consequence("do one")
        assert "C" in agent._tendency_window[0]["feedback"]
        # 专家删除 C(约束集缩小)→ 下一次 observe 后窗口里 C 应被清理
        gov.data["roles"]["测试人"] = {"A": 0.6, "B": 0.4}
        agent.observe_consequence("do two")  # changed → 立即采样并清理
        for w in agent._tendency_window:
            assert "C" not in w.get("feedback", {})
            assert "C" not in w.get("alignment", {})


class TestTendencyMathProperties:
    """倾向更新的数学性质(2026-09-26 深度体检补):归一化守恒/α 下限/被删维度消退。

    此前的回归测试覆盖"朝约束收敛"与"删除目标消退",但从未显式断言
    **Σ(value_tendency) == 1** 这个最基本的性质 —— 它一旦破坏,所有
    倾向曲线、位移分析、.goal_score 全部失真。
    """

    def _mk(self):
        import random as _r
        _r.seed(42)
        agent = _mk_agent({"A": 0.25, "B": 0.25, "C": 0.25, "D": 0.25}, window_size=10)
        gov = Governance()
        gov.data = {"roles": {"测试人": {"A": 0.5, "B": 0.3, "C": 0.2}}}
        agent.attach_governance(gov, lambda self, d: {"A": 0.5, "B": 0.3, "C": 0.2})
        return agent

    def test_sum_conservation_alpha_floor_and_dropped_extinction(self):
        import random as _r
        _r.seed(42)
        agent = self._mk()
        worst_err = 0.0
        for i in range(40):
            if i == 20:
                # 中途制度收缩:删除 C,权重向 A/B 集中
                agent._governance.data["roles"]["测试人"] = {"A": 0.6, "B": 0.4}
            fb = {"A": _r.random(), "B": _r.random()}
            if i < 20:
                fb["C"] = _r.random()
            agent._consequence_fn = lambda self, d, fb=fb: fb
            agent.observe_consequence("action-{}-{}".format(i, _r.random()))
            err = abs(sum(agent.value_tendency.values()) - 1.0)
            worst_err = max(worst_err, err)
            assert err < 1e-9, "step {}: Σ倾向偏离 1(err={:.2e})".format(i, err)
            if i >= 25:
                assert agent.value_tendency.get("C", 0.0) < 1e-9, "被删维度 C 未消退"
            if i >= 15:
                assert agent.status["tendency_meta"]["alpha"] == 0.1, "α 应触底下限 0.1"
        assert worst_err < 1e-9
