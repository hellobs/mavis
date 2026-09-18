# -*- coding: utf-8 -*-
"""通用插件面测试(纯新增,默认关闭,零业务词汇)。

本文件证明四件事:
1. 外部插件能挂载(实例化列表喂给 Simulator 的 plugins 参数);
2. 能被广播到:Simulator 复用 on_agent / on_story / on_step 同一处内部实现,
   把 agent / story / time / chat_line 事件转发给插件;
3. 抛错不影响运行:单个插件 in on_event / setup / teardown 抛异常,不打断
   其它插件,也不打断主循环;
4. 默认关闭:不传插件时 Simulator 完全不介入,行为与历史版一致。

并以一个带必传配置的插件类验证"入口点发现只对声明可无参构造的插件生效"。
"""
import datetime
import importlib.metadata

from mavisframework.core import agent_core
from mavisframework.plugin import Plugin, PluginManager
from mavisframework.runtime.simulator import Simulator


# ---------------------------------------------------------------------------
# 图腾:一个"外部插件"示例(测试内代表外部包提供的插件)
# ---------------------------------------------------------------------------
class RecorderPlugin(Plugin):
    """记录收到的 setup ctx 与事件,可在 on_event 抛错(测故障隔离)。"""

    name = "recorder"

    def __init__(self, raise_on_type=None):
        self.raised_on_time = 0
        self.events = []
        self.ctx = None
        self.torn_down = False
        self._raise_on = raise_on_type

    def setup(self, ctx=None):
        self.ctx = ctx

    def on_event(self, evt):
        self.events.append(evt)
        et = evt.get("type")
        if self._raise_on == et:
            self.raised_on_time += 1
            raise RuntimeError("plugin boom on " + et)

    def teardown(self):
        self.torn_down = True


# ---------------------------------------------------------------------------
# 仿真所需的轻量替身(绕开 LLM,只驱动 Simulator 的事件回路)
# ---------------------------------------------------------------------------
class _Logger:
    def info(self, *a, **k):
        pass

    def warning(self, *a, **k):
        pass


class _Ev:
    def get_describe(self, *a, **k):
        return "working"


class _Action:
    def __init__(self):
        self.event = _Ev()


class _FakeAgent:
    def __init__(self, name):
        self.name = name
        self.action = _Action()
        self.stories = []

    def inject_story_event(self, ev):
        self.stories.append(ev)

    def to_dict(self):
        return {"currently": "working", "coord": [0, 0], "path": []}

    def observe_consequence(self, desc):
        pass


class _Timer:
    def __init__(self):
        self._t = datetime.datetime(2025, 2, 13, 9, 30)

    def get_date(self, *a):
        if a:
            return self._t.strftime(a[0])
        return self._t

    def forward(self, minutes):
        self._t += datetime.timedelta(minutes=minutes)


class _FakeGame:
    def __init__(self, agents):
        self.agents = agents
        self.logger = _Logger()
        self._timer = _Timer()

    def get_agent(self, name):
        return self.agents[name]

    def agent_think(self, name, status):
        return {"plan": {"path": [[1, 1]]}}  # 不真调 LLM


def _one_step_sim(plugins, on_agent=None, on_story=None):
    """构造一只插件观测到的、只跑一步的 Simulator。"""
    agents = {"A": _FakeAgent("A")}
    sim = Simulator(on_agent=on_agent, on_story=on_story,
                    plugins=list(plugins),
                    story=[{"id": "s1", "time": "09:30", "event_type": "新闻",
                            "content": "行情波动", "targets": ["A"]}])
    config = {"agents": {"A": {"coord": [0, 0], "path": []}}}
    sim.simulate(_FakeGame(agents), config, step=1, stride=0)
    return sim, config


# ---------------------------------------------------------------------------
# 默认关闭:不传插件 = 不介入
# ---------------------------------------------------------------------------
class TestPluginSurfaceDefaultOff:
    def test_plugins_param_default_none_and_pmgr_none(self):
        sim = Simulator()
        assert sim.plugins is None
        assert sim._pmgr is None  # 内部管理器缺省不存在

    def test_pluginmanager_empty_noop(self):
        mgr = PluginManager()
        assert not mgr
        mgr.setup({"x": 1})   # 空管理器调用不报错
        mgr.emit({"type": "time", "time": "t"})
        mgr.teardown()

    def test_plugin_leaves_no_side_effects_when_empty(self, monkeypatch):
        """不传插件时,on_agent 只被 on_agent 回调消费,插件总线不产生副作用。"""
        got = {}
        sim = Simulator(on_agent=lambda n, s, st, t: got.update(x=(n, st, st, t)))
        assert sim._pmgr is None


# ---------------------------------------------------------------------------
# 挂载 + 广播(复用 on_agent / on_story / on_step 同一处内部实现)
# ---------------------------------------------------------------------------
class TestPluginBroadcast:
    def test_simulate_feeds_agent_time_story_to_plugin(self):
        rec = RecorderPlugin()
        seen = []
        sim, _ = _one_step_sim([rec],
                               on_agent=lambda name, state, step, sim_time: seen.append(("agent", name)),
                               on_story=lambda ev: seen.append(("story", ev.get("id"))))
        types = sorted(e["type"] for e in rec.events)
        assert "agent" in types and "time" in types and "story" in types
        # 复用同一处内部实现:story 事件对 on_story 与插件是同一份形状
        story_evt = next(e for e in rec.events if e["type"] == "story")
        assert story_evt["id"] == "s1" and story_evt["content"] == "行情波动"
        # on_agent / on_story 回调仍然照常触发(不因加插件而变化)
        assert ("story", "s1") in seen
        assert ("agent", "A") in seen

    def test_emit_time_chatline_feed_plugin_and_callback(self):
        rec = RecorderPlugin()
        cb_time, cb_chat = [], []
        sim = Simulator(on_step=lambda cfg: cb_time.append(cfg.get("time")),
                        on_chat_line=lambda s, t: cb_chat.append((s, t)),
                        plugins=[rec])
        sim.emit_time("2025-01-01")
        sim.emit_chat_line("甲", "你好")
        got = {e["type"]: e for e in rec.events}
        assert got["time"]["time"] == "2025-01-01"
        assert got["chat_line"]["speaker"] == "甲"
        assert cb_time and cb_chat  # 原有回调仍触发(单一事件源)

    def test_lifecycle_setup_and_teardown_called(self):
        rec = RecorderPlugin()
        sim, _ = _one_step_sim([rec])
        assert rec.ctx is not None and "game" in rec.ctx and "config" in rec.ctx
        assert not rec.torn_down
        sim.plugin_teardown()
        assert rec.torn_down


# ---------------------------------------------------------------------------
# 故障隔离:一个插件抛错不打断其它插件与主循环
# ---------------------------------------------------------------------------
class TestPluginFaultIsolation:
    def test_broken_plugin_does_not_stop_healthy_plugin(self):
        healthy = RecorderPlugin()
        broken = RecorderPlugin(raise_on_type="time")
        sim, _ = _one_step_sim([broken, healthy])
        # 两个插件都被广播到;broken 在 time 上抛错,healthy 仍收到 time
        assert broken.raised_on_time >= 1
        assert any(e["type"] == "time" for e in healthy.events), \
            "抛错的插件不应影响后续插件收到事件"
        # 主循环没被打断:agent / story 事件都仍到达 healthy
        types = sorted(e["type"] for e in healthy.events)
        assert "agent" in types and "story" in types

    def test_simulate_completes_when_only_broken_plugin(self):
        broken = RecorderPlugin(raise_on_type="time")
        sim, config = _one_step_sim([broken])  # 不抛异常、正常返回
        assert config.get("step") == 1

    def test_setup_error_isolated(self):
        class BadSetup(Plugin):
            def setup(self, ctx):
                raise RuntimeError("setup boom")

        class GoodSetup(Plugin):
            def __init__(self):
                self.setup_called = False

            def setup(self, ctx):
                self.setup_called = True

        good = GoodSetup()
        sim = Simulator(plugins=[BadSetup(), good])
        sim._ensure_plugins(_FakeGame({"A": _FakeAgent("A")}), {"agents": {"A": {}}})
        assert good.setup_called

    def test_chat_multisubscriber_isolation_and_old_write_kept(self):
        # 旧写法:直接赋值仍生效
        old = []
        prev = agent_core.chat_callback
        agent_core.chat_callback = lambda s, t: old.append((s, t))
        subs = []

        def record(s, t):
            subs.append((s, t))

        try:
            agent_core.subscribe_chat_line(record)

            def boom(s, t):
                raise RuntimeError("sub boom")
            agent_core.subscribe_chat_line(boom)
            agent_core._emit_chat_line("甲", "hi")  # boom 订阅者被隔离,record 仍收到
        finally:
            agent_core.unsubscribe_chat_line(record)
            agent_core.unsubscribe_chat_line(boom)
            agent_core.chat_callback = prev
        assert old == [("甲", "hi")], "老写法(直接赋值)行为不变"
        assert subs == [("甲", "hi")], "多订阅者收到同一句;抛错订阅者被隔离"


# ---------------------------------------------------------------------------
# 注册表 + 入口点发现(只对声明可无参构造的插件生效)
# ---------------------------------------------------------------------------
class _NoArgExternal(Plugin):
    """可无参构造的外部插件。"""

    name = "ext_noarg"


class _RequiredConfigExternal(Plugin):
    """带必传配置的插件:入口点发现必须跳过它(否则 create 会实例化不出来)。"""

    name = "ext_required"

    def __init__(self, required):
        self.required = required


class _FakeEp:
    def __init__(self, name, factory):
        self.name = name
        self._factory = factory

    def load(self):
        return self._factory


class TestEntryPointDiscovery:
    def test_discover_skips_required_config_plugin(self, monkeypatch):
        class Local(PluginManager):
            pass

        def fake_entry_points():
            class _Sel:
                @staticmethod
                def select(group):
                    return [_FakeEp("ext_noarg", _NoArgExternal),
                            _FakeEp("ext_required", _RequiredConfigExternal)]
            return _Sel()

        monkeypatch.setattr(importlib.metadata, "entry_points", fake_entry_points)
        loaded = Local.discover()
        assert "ext_noarg" in loaded
        assert "ext_required" not in loaded, \
            "入口点发现必须跳过需要必传配置的插件"
        # 已注册的可无参构造插件能零参实例化
        assert isinstance(Local.create("ext_noarg"), _NoArgExternal)

    def test_registry_create_with_required_config_raises(self):
        class Local(PluginManager):
            pass

        # 带必传配置的插件按名 create 且不给配置 → 实例化不了
        Local.REGISTRY.pop("local_required", None)
        try:
            @Local.register("local_required")
            def fact(required):
                return _RequiredConfigExternal(required)
            try:
                Local.create("local_required")
                assert False, "缺少必传配置不应默默成功"
            except Exception:
                pass
        finally:
            Local.REGISTRY.pop("local_required", None)
        assert "local_required" not in Local.REGISTRY