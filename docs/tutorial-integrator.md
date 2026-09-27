# 教程:基于 mavis 二次开发(接入方上手指南)

这份文档给"要在 mavis 之上做自己的东西"的人一条最短路径:你不需要读懂框架内部,
只需要认几个稳定的接入点。本页只讲**怎么动手**——四种常见形态各自的最小骨架、
你必须自己提供什么、以及最容易踩的坑。契约细节(签名、默认值、已知限制)在
[扩展面](tutorial-extension.md),两页配合读。

## 0. 先装好

```bash
git clone https://github.com/hellobs/mavis.git
cd mavis
pip install -e .        # 开发期可编辑安装;发布期用 python -m build 后装 wheel
pytest tests            # 密封测试,不依赖 Ollama,秒级
```

Python ≥ 3.12;运行依赖只有 `pydantic` 与 `requests`。

要清楚一件事:**mavis 是内核,不含业务,也不含示例资产**(角色目录、`maze.json`、
剧情与关系配置)。所以"能不能跑起来"取决于你提供什么——下面四种形态的区别正在这里。

## 一、先认清你要做哪一种

- **形态 1,写一个接入方应用/平台**:你提供资产、配置与 LLM,用 `Game` + `Simulator`
  跑推演,自己决定怎么展示与落盘。参考实现是 Provenance 平台的实时服务 `live_fastapi.py`
  (FastAPI + WebSocket 消费框架契约消息)。
- **形态 2,写一个插件包**:你只消费"事件字典",不碰框架内部;观察者、可视化、落盘、
  审计都适合做成插件。参考实现是 `mavis-vizkit`(独立包,运行时**不 import 框架**)。
- **形态 3,写一个前端/渲染后端**:你只认消息协议(`mavisframework.runtime.protocol`),
  transport 自选(SSE / WebSocket)。现有 Phaser 壳与计划中的 Unity 壳都属这一类。
- **形态 4,接治理层(可选)**:用 `governance` + `consequence_fn` 让 agent 的价值倾向
  可被外生约束影响;要换真实市场模型,只替换一个可调用对象。

四种可以组合:一个平台应用里挂若干插件、自己写前端、再可选接治理层。关键约定是
**每一层只依赖下一层的稳定面**——所以换前端不动内核,加插件不动业务。

## 二、形态 1:最小可跑的应用

```python
import mavisframework as mf

# 1) 你要准备的资产:角色目录(agent.json)、maze.json、剧情/关系配置
cfg = mf.load_config("20250213-09:30", 2, ["角色A", "角色B"],
                     assets_root="assets/village")

# 2) timer 必须显式传:否则用墙钟,运行结果会被真实时间影响
timer = mf.Timer(start=cfg["time"]["start"])
game = mf.Game("demo", "frontend/static", cfg, {}, timer=timer)
game.reset_game()          # LLM provider 在 reset 时创建(见第八节)

sim = mf.Simulator(
    max_workers=len(game.agents),
    story=story_events,                                   # 剧情事件(可空)
    on_agent=lambda name, state, step, sim_time: None,    # 只读通知:落盘 / 推流
    on_step=lambda config: None,
    export_decisions=True, decisions_path="results/decisions.json",
)
sim.simulate(game, cfg, step=1, stride=2, start_step=0,
             checkpoints_folder="results/checkpoints/demo")
```

接入方必须自己决定的三件事:

- **资产与配置**:框架只按路径读,不猜默认值;`assets_root` 是相对根,会拼到
  `static_root` 之后(即 `frontend/static/assets/village/...`)。
- **LLM**:不配置就没有可用 provider,框架会抛出明确错误提示你去配(Ollama 本地,
  或任意 OpenAI 兼容 API)。
- **节奏**:框架不抢轮次。要一步一步看就循环 `simulate(step=1)`;要跑到底就 `step=N`。

## 三、形态 2:最小插件包(推荐先从这个入手)

插件只需认三件事:三个可选方法、一份事件字典、一个入口点组名。

```python
from mavisframework.plugin import Plugin

class CountPlugin(Plugin):
    name = "count"

    def __init__(self, limit=3):
        self.limit = limit
        self.lines = []

    def setup(self, ctx=None):       # ctx 由挂载方填充(game / config / simulator),框架只透传
        if ctx and "limit" in ctx:
            self.limit = ctx["limit"]

    def on_event(self, evt):         # agent / time / chat_line / story
        if evt.get("type") == "chat_line":
            self.lines.append((evt["speaker"], evt["text"]))

    def teardown(self):              # 收尾:关连接 / 刷盘
        print("收到", len(self.lines), "句对话")

sim = mf.Simulator(..., plugins=[CountPlugin()])   # 传实例:实例自带配置,推荐
# ... 跑完 ...
sim.plugin_teardown()                              # 显式收尾(先退订对话订阅,再 teardown)
```

事件类型与键名:`time` / `chat_line` / `story` 与 `mavisframework.runtime.protocol` 一致;
`agent` 是**框架原生子集**(`name` / `coord` / `path` / `time`,外加原始 `state`);
`init` / `snapshot` 框架不产生,由可视化层自行构造。总线上的事件 dict 与 `on_*` 回调、
`config["agents"]` 可能共用同一引用,框架在后续步骤还会改动——所以插件应
**只读事件**,不要原地修改,也不要长期持有当作稳定快照。

要发成"可被发现的插件包",在你自己的 `pyproject.toml` 里声明入口点:

```toml
[project.entry-points."mavisframework.plugins"]
count = "my_plugin.count:CountPlugin"
```

发现与实例化是**两步**(框架不会替你自动挂载):

```python
from mavisframework.plugin import PluginManager

PluginManager.discover()                    # 读入口点,只登记"可无参构造"的工厂
pmgr = PluginManager()
pmgr.mount_by_name("count", limit=5)        # 带必传配置的插件在这里显式传参
sim = mf.Simulator(..., plugins=pmgr)       # 也可以直接把 PluginManager 传进去
```

带必传配置的插件**不会**被入口点自动实例化(签名里有无默认参数即跳过,并记一条 warning);
这时只能像上面那样传实例或显式 `mount_by_name(..., **配置)`。

故障隔离:单个插件在 `setup` / `on_event` / `teardown` 抛异常只记 warning,不打断其它插件,
也不打断主循环。反过来也请遵守:插件**不要**把异常静默吞掉——"页面上什么都没有、
日志里也什么都没有"是最难查的故障。

参考实现:`mavis-vizkit`(独立包,随平台仓 `packages/mavis-vizkit` 发布)。
它自带注册表 `mavis_vizkit.register(name, factory)` 与 `Fanout`(多插件分发,默认报错不静默),
内置 `console` / `report` / `town` / `live` 四个可视化插件,并另有第三方入口点组
`mavis_vizkit.plugins`。**新增一个可视化插件 = 写一个 `Visualizer` 子类 + 一行
`register(...)`**——它既是 mavis 插件面的用法示范,也是"包与包之间只共享事件字典"的样例。

## 四、形态 3:只认协议的前端

渲染层不需要 import 框架。契约消息定义在 `mavisframework.runtime.protocol`:
`AgentState`(坐标/路径/动作)、`TimeMsg`、`ChatLineMsg`、`SnapshotMsg`(新连接补齐)、
`DecisionEvent`(供治理平台/专家界面)。消费端可用 `validate_message` 做入口校验。
transport 无关:SSE 与 WebSocket 送的是同一批消息;Phaser 壳与计划中的 Unity 壳是
同一协议的两种渲染实现。

## 五、形态 4:接治理层(可选)

`governance`(制度约束,`{goal: weight}`)与 `consequence_fn`(后果反馈,
`(agent, action_desc) -> {goal: feedback}`)是两个可注入点,**不传就完全不介入**。
约束不进提示词、不强制行动,只加权后果反馈,因此调整约束后倾向要经后续体验才收敛
(滞后收敛本身就是内化证据)。默认实现用 embedding 相似度做轻量替代;要接真实市场模型,
换掉这个可调用对象即可,其余链路不变。机制与更新数学见 [IVD 教程](tutorial-ivd.md)。

## 六、有需求进来了,按这个顺序办

1. **配置能不能解决?**(`agent.json` / `maze.json` / `story.json` / 运行参数)
2. **既有扩展点能不能解决?**([扩展面](tutorial-extension.md) 的清单)
3. **你在自己那边能不能解决?**——多数情况可以(例:随机性由你自己 `random.seed(n)`;
   实验节奏由你逐步 `simulate(step=1)` 决定,框架不抢轮次)。
4. 都不行,才考虑给框架新增一个**通用**扩展点:纯新增、默认关闭、语义中立、带单测。
5. **需要改动既有行为语义 → 不做。**宁可你在自己那边绕一下——这条是 mavis 对外
   承诺的基础。

## 七、交出改动前自查

- `pytest tests` 全绿,其中 `tests/test_extension_surface.py` 锁住签名、默认值与两条纯洁度约定;
- 不配置新能力时,行为与历史版本逐位一致(默认关闭);
- 框架源码里不出现你的业务词汇(case01 / 角色名 / 公司名等);
- 改过框架就同步更新 [扩展面](tutorial-extension.md) 与 README 的扩展面小节。

## 八、已知限制(如实写,别当承诺)

- 随机数走全局 `random`,没有 seed 参数;`Agent.think` 走线程池并行,跨角色消费随机的
  顺序不确定——`random.seed()` 能收敛,但做不到逐样本可复现。
- 下划线成员(`agent._timer`、`agent._governance` 等)不在契约内,不保证稳定。
- `Game` 的 LLM provider 是惰性的:构造后不调一次 `reset_game()`,首次使用会报"缺少可用 LLM"。
- 框架不带资产:没有角色目录与 `maze.json`,就没有可跑的模拟。