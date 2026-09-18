# -*- coding: utf-8 -*-
"""扩展面契约测试:锁死 mavis 对外的稳定接入点,并锁住两条纯洁度约束。

为什么要这个文件
----------------
case01(受控实验)与 provenance 平台**只**通过下面这几类点接入 mavis:

- 配置:`load_config(...)` 与各角色的 `agent.json`;
- 容器:`Game(name, static_root, config, conversation, timer=, governance=, consequence_fn=)`;
- 调度:`Simulator(..., external_state=, interaction_request=, on_agent=, on_step=, on_story=)`
  与 `Simulator.register_condition("<type>")` 注册表;
- 角色:`Agent` 的公开方法与三个新增字段(`role_directive` / 步级上下文 / 交互请求);
- 模块级钩子:`mavisframework.core.agent_core.chat_callback`。

这些点一旦被改签名、改默认值或删掉,接入方只会在**运行期**才炸。
把它们写成契约测试,CI 里就会立刻炸,于是 case01 不需要每次为了"能不能这么做"
去和 mavis 谈一次边界——这就是"mavis 保持纯洁、case01 又能从容"的机制化做法。

同时锁两条纯洁度约束:

1. 框架源码里不出现 case01 专属业务词汇(通用演示词汇如"投资顾问"不算);
2. 新增能力一律默认关闭:不配置时属性为 `None` / 空 / `False`,行为与历史版本一致。

改动本文件前先读 `docs/tutorial-extension.md`(扩展面说明)。
"""
import datetime
import inspect
import os

import mavisframework
from mavisframework.config.loader import load_config
from mavisframework.core import agent_core
from mavisframework.core.agent_core import Agent
from mavisframework.plugin import Plugin, PluginManager
from mavisframework.runtime.game import Game
from mavisframework.runtime.simulator import Simulator

# 只列 case01 专属词汇;场景通用词(AI Advisor / 投资顾问 / the Ville)不算越界
CASE01_TOKENS = ("case01", "case 01", "ethan", "investment ai", "hcm")


# ---------------------------------------------------------------------------
# 最小替身:只为了在不起真实 Game 的前提下构造一个 Agent
# ---------------------------------------------------------------------------
class _Tile:
    def __init__(self, coord=(0, 0)):
        self.coord = list(coord)
        self.address = ["the Ville", "测试区"]

    def get_address(self, *a, **kw):
        return list(self.address) if kw.get("as_list") else ":".join(self.address)

    def has_address(self, *a):
        return False

    def update_events(self, ev):
        return False

    def add_event(self, ev):
        return None

    def remove_events(self, **kw):
        return None

    def get_events(self):
        return []

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


def _mk_agent(**extra):
    cfg = {
        "name": "测试人",
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
    return Agent(cfg, _Maze(), {}, timer=_Timer())


def _params(fn):
    return inspect.signature(fn).parameters


# ---------------------------------------------------------------------------
# 契约:构造参数
# ---------------------------------------------------------------------------
def test_simulator_public_signature_stable():
    p = _params(Simulator.__init__)
    for name in ("on_agent", "on_step", "on_chat_line", "on_story", "max_workers",
                 "export_decisions", "decisions_path", "roles", "story", "stride",
                 "external_state", "interaction_request", "plugins"):
        assert name in p, "Simulator 少了构造参数: {}".format(name)
    assert p["external_state"].default is None
    assert p["interaction_request"].default is None
    assert p["on_agent"].default is None and p["on_step"].default is None
    assert p["on_story"].default is None
    # 通用插件面:默认 None = 完全不介入
    assert p["plugins"].default is None


def test_simulate_public_signature_stable():
    p = _params(Simulator.simulate)
    for name in ("game", "config", "step", "stride", "start_step",
                 "checkpoints_folder", "on_step", "on_agent"):
        assert name in p, "Simulator.simulate 少了参数: {}".format(name)


def test_game_public_signature_stable():
    p = _params(Game.__init__)
    for name in ("name", "static_root", "config", "conversation", "timer",
                 "logger", "governance", "consequence_fn"):
        assert name in p, "Game 少了构造参数: {}".format(name)
    # 三个可选面默认都不介入:不传 = 历史行为
    assert p["timer"].default is None
    assert p["governance"].default is None
    assert p["consequence_fn"].default is None


def test_load_config_public_signature_stable():
    p = _params(load_config)
    for name in ("start_time", "stride", "agents", "config_path", "assets_root"):
        assert name in p, "load_config 少了参数: {}".format(name)


# ---------------------------------------------------------------------------
# 契约:默认关闭(不配置 = 零行为变化)
# ---------------------------------------------------------------------------
def test_simulator_hooks_default_off():
    sim = Simulator()
    assert sim.external_state is None
    assert sim.interaction_request is None
    assert sim.interactions == []          # 审计表默认空
    assert sim.story == []                 # 没配剧情就不注入


def test_agent_new_fields_default_off():
    agent = _mk_agent()
    assert agent.role_directive == ""          # 不配角色指令 → 提示词不加块
    assert agent.step_context() == {}          # 未注入 → 空
    agent.set_step_context({"stress": "high"})
    assert agent.step_context() == {"stress": "high"}
    agent.set_step_context(None)               # None = 清除
    assert agent.step_context() == {}
    # Location 转移:唯一的句柄就是这个名字,故意直接读它来锁"默认关闭"
    assert getattr(agent, "_transfer_enabled") is False


def test_agent_public_surface_exists():
    agent = _mk_agent()
    for name in ("reset", "attach_governance", "get_constraints", "get_tendency",
                 "set_step_context", "request_interaction", "move", "make_schedule",
                 "think", "percept", "make_plan", "reflect", "find_path",
                 "inject_story_event", "recent_story_events", "to_dict"):
        assert callable(getattr(Agent, name, None)), "Agent 少了公开方法: {}".format(name)
    # 实例属性型公开面(在 __init__ 里设置,不在类字典里)
    for name in ("coord", "path", "action", "status", "role_directive"):
        assert hasattr(agent, name), "Agent 实例少了公开属性: {}".format(name)


# ---------------------------------------------------------------------------
# 契约:扩展点注册表与模块级钩子
# ---------------------------------------------------------------------------
def test_register_condition_decorator_and_registry():
    key = "contract_surface_probe"
    assert key not in Simulator.CONDITION_CHECKERS
    try:
        @Simulator.register_condition(key)
        def _checker(game, ev):
            return True

        assert Simulator.CONDITION_CHECKERS[key] is _checker
    finally:
        Simulator.CONDITION_CHECKERS.pop(key, None)
    assert key not in Simulator.CONDITION_CHECKERS


def test_chat_callback_module_hook_exists():
    assert hasattr(agent_core, "chat_callback")


# ---------------------------------------------------------------------------
# 契约:通用插件面(Plugin / PluginManager)
# ---------------------------------------------------------------------------
def test_plugin_base_interface_signature_and_default_noop():
    """Plugin 三个方法都可选:签名是 (self, ctx=None)/(self, evt)/(self),缺省 no-op。"""
    for meth in ("setup", "on_event", "teardown"):
        assert callable(getattr(Plugin, meth, None)), "Plugin 少了方法: {}".format(meth)
    p = Plugin()
    # 三个方法不抛、也不要求返回值(接入口缺省行为 = 什么都不做)
    p.setup({"game": 1, "config": {}})   # 用元数据 ctx 调用不会炸
    p.on_event({"type": "time", "time": "t"})
    p.teardown()


def test_pluginmanager_surface_and_empty_registry():
    """PluginManager 的公开面齐全;默认注册表为空、空管理器调用无副作用。"""
    for meth in ("register", "names", "create", "discover",
                 "mount", "mount_by_name", "setup", "emit", "teardown"):
        assert callable(getattr(PluginManager, meth, None)), \
            "PluginManager 少了方法: {}".format(meth)
    assert isinstance(PluginManager.REGISTRY, dict)
    # 框架不预注册任何插件:mavis 不认识具体插件,入口点组由外部包自报
    assert "ext_required" not in PluginManager.REGISTRY
    mgr = PluginManager()
    assert not mgr                      # 空管理器为 falsy
    assert mgr.plugins == []
    mgr.setup({"game": 1})              # 空操作,不抛
    mgr.emit({"type": "time", "time": "t"})
    mgr.teardown()


def test_simulator_plugins_default_off_no_pmgr():
    """不传 plugins 时 Simulator 不建内部管理器,行为与历史版一致。"""
    sim = Simulator()
    assert sim.plugins is None
    assert sim._pmgr is None            # 无插件 = 无管理器 = 完全不介入
    assert len(sim.interactions) == 0


# ---------------------------------------------------------------------------
# 纯洁度
# ---------------------------------------------------------------------------
def test_framework_ships_no_case01_condition():
    """框架本身不注册任何 case01 专属条件;注册由接入方自己做。"""
    assert "case01_node" not in Simulator.CONDITION_CHECKERS


def test_framework_source_has_no_case01_vocabulary():
    root = os.path.dirname(os.path.abspath(mavisframework.__file__))
    hits = []
    for dirpath, _dirs, files in os.walk(root):
        for filename in files:
            if not filename.endswith(".py"):
                continue
            path = os.path.join(dirpath, filename)
            with open(path, encoding="utf-8") as f:
                text = f.read().lower()
            for token in CASE01_TOKENS:
                if token in text:
                    hits.append("{}: {}".format(os.path.relpath(path, root), token))
    assert hits == [], "框架源码里出现了 case01 专属词汇: {}".format(hits)
