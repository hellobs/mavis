# -*- coding: utf-8 -*-
"""外部注入钩子测试(三处纯新增能力,默认关闭时零回归)。

覆盖:
1. 角色指令 / 步级外部状态进入提示词;两者为空时输出与基线一致。
2. 未提供钩子时 Simulator 行为不变(属性为 None、无交互记录、不触碰 game)。
3. 交互请求入口:forced=True 跳过冷却与"是否想聊"概率门;非 forced 仍受冷却限制。
"""
import datetime

from mavisframework.core.agent_core import Agent
from mavisframework.runtime.simulator import Simulator


class _Ev:
    def __init__(self, subject="测试人"):
        self.subject = subject
        self.address = ["the Ville", "测试区"]
        self.predicate = "待命"
        self.object = ""

    def fit(self, *args, **kwargs):
        return False

    def get_describe(self, *args, **kwargs):
        return "idle"


class _Action:
    def __init__(self, subject):
        self.event = _Ev(subject)
        self.obj_event = None


class _Tile:
    def __init__(self, coord=(0, 0)):
        self.coord = list(coord)
        self.events = []
        self.address = ["the Ville", "测试区"]

    def get_address(self, *a, **kw):
        return list(self.address) if kw.get("as_list") else ":".join(self.address)

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
    def __init__(self):
        self._t = datetime.datetime(2025, 2, 13, 9, 30)

    def daily_duration(self, **kw):
        return 0

    def get_date(self, *a):
        if a:
            return self._t.strftime(a[0])
        return self._t

    def get_delta(self, start, end=None, mode="minute"):
        end = end or self._t
        return int((end - start).total_seconds() // 60)

    def forward(self, minutes):
        self._t += datetime.timedelta(minutes=minutes)

    def daily_time(self, duration):
        base = self._t.replace(hour=0, minute=0, second=0, microsecond=0)
        return base + datetime.timedelta(minutes=duration)

    def daily_format_cn(self):
        return "2025年2月13日"


def _mk_agent(name="测试人", **extra):
    cfg = {
        "name": name,
        "currently": "working",
        "coord": [0, 0],
        "initial_tendency": {},
        "percept": {"att_bandwidth": 4},
        "think": {"llm": {"provider": "ollama"}, "tendency_window": 15},
        "chat_iter": 1,
        "chat_cooldown_min": 20,
        "chat_retry_prob": 0.5,
        "spatial": {"address": {}, "tree": {}},
        "schedule": {},
        "associate": {"embedding": {"provider": "simple"}},
        "scratch": {
            "age": 30,
            "innate": "cautious",
            "learned": "finance",
            "lifestyle": "busy",
            "daily_plan": "work",
        },
        "storage_root": "",
        "role_type": "user",
    }
    cfg.update(extra)
    agent = Agent(cfg, _Maze(), {}, timer=_Timer())
    agent.action = _Action(name)
    agent.schedule.daily_schedule = [{"describe": "work", "decompose": []}]
    return agent


class _ChatNode:
    create = datetime.datetime(2025, 2, 13, 9, 30)


def _prepare_pair():
    """构造两个可对话的 agent:静止、清醒、有日程、冷却内刚聊过。"""
    a, b = _mk_agent("甲"), _mk_agent("乙")
    for agent in (a, b):
        agent._skip_react = lambda other: False
        agent.path = []
        agent.associate.retrieve_chats = lambda name=None: [_ChatNode()]
        agent.schedule_chat = lambda *args, **kwargs: None
    calls = []

    def _stub(hint, *args, **kwargs):
        calls.append(hint)
        return "摘要" if hint == "summarize_chats" else "同意"

    a.completion = _stub
    b.completion = _stub
    return a, b, calls


class TestEnvironmentBlocks:
    def test_prompt_unchanged_without_environment_blocks(self):
        agent = _mk_agent()
        desc = agent.scratch._base_desc()
        assert "provided by the environment" not in desc
        assert desc.strip().endswith("must be in English.")

    def test_role_directive_enters_prompt(self):
        agent = _mk_agent(role_directive="Answer with a structured recommendation.")
        desc = agent.scratch._base_desc()
        assert "Role instruction (provided by the environment)" in desc
        assert "structured recommendation" in desc

    def test_step_context_enters_prompt_and_clears(self):
        agent = _mk_agent()
        agent.set_step_context({"stress": 0.7, "suspicion": "medium"})
        desc = agent.scratch._base_desc()
        assert "Current situation (provided by the environment)" in desc
        assert "stress: 0.7" in desc
        assert "suspicion: medium" in desc
        assert agent.step_context() == {"stress": 0.7, "suspicion": "medium"}

        agent.set_step_context(None)
        assert agent.step_context() == {}
        assert "Current situation" not in agent.scratch._base_desc()


class TestSimulatorHooksDefaultOff:
    def test_hooks_default_none(self):
        sim = Simulator()
        assert sim.external_state is None
        assert sim.interaction_request is None
        assert sim.interactions == []

    def test_apply_hooks_noop_when_disabled(self):
        sim = Simulator()
        # game 传 None:若实现触碰 game 会立刻抛错
        sim._apply_injection_hooks(None, {}, 1, "20250213-09:30")
        assert sim.interactions == []


class TestForcedInteraction:
    def test_forced_bypasses_cooldown_and_decide_gate(self):
        a, b, calls = _prepare_pair()
        assert a.request_interaction(b, "咨询主题") is True
        assert "decide_chat" not in calls
        assert a.conversation, "对话应被记录"

    def test_not_forced_respects_cooldown(self):
        a, b, calls = _prepare_pair()
        assert a._chat_with(b, "咨询主题") is False
        assert "decide_chat" not in calls
        assert not a.conversation
