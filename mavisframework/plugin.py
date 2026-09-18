# -*- coding: utf-8 -*-
"""framework.plugin — 通用插件面(纯新增,默认关闭,零业务词汇)

任何外部包都能作为插件挂进 mavis,而 mavis 自己不认识任何具体插件。
mavis 只认识三件事:插件可选实现的三个方法、一份"事件字典"契约、一个入口点组名。

约定(对应 docs/tutorial-extension.md 的"新增能力硬约定"):
1. **默认关闭**:不注册插件时,本模块与 Simulator 完全不介入,行为与历史版逐位一致。
2. **零业务词汇**:本文件不出现任何接入方业务词。
3. **故障隔离**:单个插件抛异常只记 warning,不打断其它插件,也不打断主循环。

事件字典复用 mavisframework.runtime.protocol 的键名。框架原生产生的协议事件是
agent / time / chat_line / story;init 与 snapshot 由消费方(如可视化层)自行构造,
框架不产生,故框架总线不广播这两种。

生命周期:setup 在插件挂进运行上下文时调一次(默认惰性,在 Simulator 首次 simulate 时),
teardown 由挂载方在运行收尾时显式触发。
"""
import inspect
import logging
from typing import Callable, Dict, List, Optional

# 入口点组名:外部包在 pyproject 里声明即被发现
_PLUGIN_GROUP = "mavisframework.plugins"

_log = logging.getLogger("mavisframework.plugin")


class Plugin:
    """插件基类。三个方法都可选实现(缺省 no-op)。"""

    name = "base"

    def setup(self, ctx: Optional[dict] = None) -> None:
        """挂载进运行上下文时调用一次。

        ctx 由挂载方填充,通常含 game / config / simulator 等句柄;内容不约定、
        由接入方自定义,框架只负责透传。
        """

    def on_event(self, evt: dict) -> None:
        """收到一个协议事件(agent / time / chat_line / story)。"""

    def teardown(self) -> None:
        """运行收尾时调用一次(关闭连接 / 刷盘)。"""


def _is_no_arg_constructible(factory: Callable) -> bool:
    """判断工厂(类或工厂函数)是否"声明可无参构造"。

    inspect.signature 对类会去掉 self。只要签名里没有必需参数(self 之外的
    无默认位置/关键字参数)即视为可无参构造。带必传配置的插件(如 town/report
    有必需参数)不会被入口点发现自动实例化。
    """
    try:
        sig = inspect.signature(factory)
    except (TypeError, ValueError):
        return True  # 拿不到签名就乐观放行(由挂载期错误隔离兜底)
    for p in sig.parameters.values():
        if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY):
            if p.default is p.empty:
                return False
    return True


class PluginManager:
    """持有一组插件实例,对 setup / emit / teardown 做逐插件错误隔离。

    也提供按名注册表 + 入口点发现(组 mavisframework.plugins):
    发现只对"声明可无参构造"的插件生效,带必传配置的插件因签名含必需参数会被跳过。
    """

    REGISTRY: Dict[str, Callable[..., Plugin]] = {}

    __slots__ = ("_plugins", "_started")

    def __init__(self, plugins: Optional[List[Plugin]] = None):
        self._plugins: List[Plugin] = []
        self._started = False
        for p in plugins or []:
            self.mount(p)

    # ------------------------------------------------------------------
    # 注册表与发现
    # ------------------------------------------------------------------
    @classmethod
    def register(cls, name: str) -> Callable:
        """装饰器:把一个工厂(类或工厂函数)注册到 `name` 名下。"""

        def deco(factory: Callable[..., Plugin]):
            cls.REGISTRY[name] = factory
            return factory

        return deco

    @classmethod
    def names(cls) -> List[str]:
        return sorted(cls.REGISTRY)

    @classmethod
    def create(cls, name: str, **kwargs) -> Plugin:
        """按名实例化。名字未注册抛 KeyError;缺必传配置由工厂自己抛错。"""
        if name not in cls.REGISTRY:
            raise KeyError(
                "未知插件 '{}';已注册: {}".format(name, cls.names()))
        return cls.REGISTRY[name](**kwargs)

    @classmethod
    def discover(cls) -> List[str]:
        """入口点发现:只注册"声明可无参构造"的插件工厂,返回新注册的名字。"""
        loaded: List[str] = []
        try:
            from importlib.metadata import entry_points
        except ImportError:
            return loaded
        try:
            eps = entry_points()
            group = (eps.select(group=_PLUGIN_GROUP) if hasattr(eps, "select")
                     else eps.get(_PLUGIN_GROUP, []))
        except Exception:
            return loaded
        for ep in group:
            if ep.name in cls.REGISTRY:
                continue
            try:
                factory = ep.load()
            except Exception:
                continue
            if not _is_no_arg_constructible(factory):
                _log.warning("插件 '%s' 需要必传配置,入口点发现跳过", ep.name)
                continue
            cls.REGISTRY[ep.name] = factory
            loaded.append(ep.name)
        return loaded

    # ------------------------------------------------------------------
    # 挂载
    # ------------------------------------------------------------------
    def mount(self, plugin: Plugin) -> Plugin:
        """追加一个插件实例(实例天然带着它的配置)。"""
        self._plugins.append(plugin)
        return plugin

    def mount_by_name(self, name: str, **kwargs) -> Plugin:
        """按名创建实例并挂载(带必传配置的插件在此显式传 kwargs)。"""
        return self.mount(self.create(name, **kwargs))

    @property
    def plugins(self) -> List[Plugin]:
        return list(self._plugins)

    def __len__(self) -> int:
        return len(self._plugins)

    def __bool__(self) -> bool:
        return bool(self._plugins)

    # ------------------------------------------------------------------
    # 生命周期与事件广播
    # ------------------------------------------------------------------
    def setup(self, ctx: Optional[dict] = None) -> None:
        """对所有插件调一次 setup(幂等)。"""
        if self._started:
            return
        self._started = True
        for p in self._plugins:
            try:
                p.setup(ctx)
            except Exception as e:  # noqa: BLE001
                _log.warning("plugin '%s' setup failed: %s", p.name, e)

    def emit(self, evt: dict) -> None:
        """把事件广播给所有插件;单个插件抛错只记 warning,不打断其它插件。"""
        if not self._plugins:
            return
        for p in self._plugins:
            try:
                p.on_event(evt)
            except Exception as e:  # noqa: BLE001
                _log.warning("plugin '%s' on_event failed: %s", p.name, e)

    def teardown(self) -> None:
        """对所有插件调 teardown,随后清空,后续 emit 不再生效。"""
        for p in self._plugins:
            try:
                p.teardown()
            except Exception as e:  # noqa: BLE001
                _log.warning("plugin '%s' teardown failed: %s", p.name, e)
        self._plugins = []
        self._started = False