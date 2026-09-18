# 教程:扩展面(接入方可以依赖的稳定 API)

mavisframework 本身不装任何业务逻辑:角色、场景、剧情、外部世界事实都由接入方提供。
这份文档把**接入方可以依赖的那层 API** 写清楚——它是 mavis 与业务之间的合同。

为什么要单独写一份:接入方(case01 受控实验、provenance 平台)只需要一小撮稳定的点;
不写清楚,每遇到一个新需求都得重新谈一次"能不能这么接"。写清楚之后,接入方的活动空间
就是下面这层**已经存在**的面,不需要为了一个新能力去改框架。

配套:`tests/test_extension_surface.py` 把这些点写成契约测试(签名、默认值、注册表、
纯洁度),改框架时它会先炸。

## 一、接入方式总览

配置层:`load_config(...)`、各角色 `agent.json`、若干环境变量。
容器层:`Game(...)`。
调度层:`Simulator(...)` 与 `Simulator.register_condition(...)`。
角色层:`Agent` 的公开方法与三个可选字段。
进程级:`mavisframework.core.agent_core.chat_callback`(`chat_line` 逐句,支持多订阅)。
插件层:`mavisframework.plugin.Plugin` / `PluginManager`(可选,默认关闭,见 §8)。

## 二、逐项说明

### 1. 配置:`load_config(...)`

```
load_config(start_time="20240213-09:30", stride=15, agents=None,
            config_path=None, assets_root=None) -> dict
```

- `start_time`:模拟时钟起点,格式 `YYYYMMDD-HH:MM`。**这是让运行可复现的关键之一**:
  不指定就用默认值,与真实时间无关(不同于 `Game` 默认的墙钟 `Timer()`)。
- `stride`:每步推进的模拟分钟数。
- `agents`:要加载的角色名列表;不传则读 assets 下的全部角色。
- `config_path`:`agent_base` 配置(LLM 等默认参数)的 JSON 路径。
- `assets_root`:静态资源根(角色目录、地图等)。

相关环境变量:`MAVIS_ASSETS_ROOT`(资源根)、`MAVIS_CONFIG_PATH`(agent_base)、
`MAVIS_CHECKPOINTS_ROOT`(存档根,不设置则落在 `results/checkpoints`)。

### 2. 容器:`Game(...)`

```
Game(name, static_root, config, conversation, timer=None, logger=None,
     governance=None, consequence_fn=None)
```

- `timer`:不传时用墙钟 `Timer()`。**接入方若要求"模拟时间可控",必须显式传
  `Timer("<起点的 YYYYMMDD-HH:MM>")`**——否则运行成败会被真实时间影响(例如对话在
  23:00 后不发起)。
- `governance` / `consequence_fn`:IVD 的制度约束与后果反馈,不传即完全不介入。
- 角色实例在构造时按 `config["agents"]` 建立,并在这里挂接 governance。

### 3. 调度:`Simulator(...)`

```
Simulator(on_agent=None, on_step=None, on_chat_line=None, on_story=None,
          max_workers=5, llm_concurrency=0, export_decisions=False,
          decisions_path="", roles=None, story=None, stride=2,
          external_state=None, interaction_request=None, plugins=None)
```

- 回调:`on_agent(name, state, step, sim_time)`、`on_step(config)`、
  `on_story(event_dict)` 是**只读通知**,接入方在这里做可视化/落盘。
- `story`:剧情事件列表;事件可带 `condition` 走注册的条件判定(见下一条)。
- `external_state(name, step, sim_time, game) -> dict`:步级外部状态,**只进提示词、
  不写记忆**;不提供时行为与历史版本完全一致。
- `interaction_request(step, sim_time, game) -> [{from, to, focus}]`:外部发起的交互请求;
  它会跳过冷却与"想不想聊"的概率门,但其余前置条件(清醒、未在移动、不在对话中)仍生效,
  **对话内容仍由 LLM 生成**。

`Simulator.simulate(game, config, step, stride=0, start_step=0,
checkpoints_folder="", on_step=None, on_agent=None)`:推进若干步。

### 4. 条件注册:`Simulator.register_condition("<type>")`

```
@Simulator.register_condition("my_condition")
def _check(game, ev) -> bool: ...
```

注册后,story 事件写 `{"condition": {"type": "my_condition", ...}}` 即由该函数判定是否触发。
注册表是 `Simulator.CONDITION_CHECKERS`。**框架自己不带任何业务条件类型**——
`case01_node` 之类由接入方在自己进程里注册。同一事件只触发一次(按 id 去重)。

### 5. 角色配置字段

- `role_directive`(字符串):非空时以追加块进入该角色的提示词,用于回答风格/角色约束;
  为空时零变化。
- `coord` / `path`:初始坐标与路径。
- `think.llm`:角色级 LLM 覆盖(provider / model / base_url / api_key),可让不同角色
  走不同后端。
- `transfer.enabled`(布尔,**默认 `False`**):Location 转移(演示/可视化用的"主动换场")。
  默认关闭;受控实验不要开启,靠钉定坐标保证关键交互。

### 6. 角色公开方法

- `set_step_context(dict)` / `step_context()`:读写本步外部状态(只进提示词)。
- `request_interaction(other, focus="") -> bool`:请求一次交互,返回是否发生。
- `move(coord, path=None)` / `make_schedule()` / `find_path(agents)`:位置与日程。
- `to_dict(with_action=True)`:当前状态快照(可视化与落盘用)。
- `get_tendency()` / `get_constraints()` / `goal_alignment(action)`:IVD 读侧。
- `attach_governance(governance, consequence_fn=None)`:由 `Game` 调用。
- `inject_story_event(event)` / `recent_story_events(topk=2)`:剧情记忆写入与读取。
- `is_awake()` / `llm_available()`:状态查询。

### 7. 进程级钩子:`agent_core.chat_callback`(对话逐句)

老写法(直接赋值)仍然有效:

```
import mavisframework.core.agent_core as agent_core
agent_core.chat_callback = lambda speaker, text: ...
```

多订阅者写法(推荐;与老写法共存,互不顶掉):

```
agent_core.subscribe_chat_line(fn)     # fn(speaker, text)
agent_core.unsubscribe_chat_line(fn)
```

每产生一句对话,老写法回调与所有订阅者**各收到一次**——对话事件由 `_emit_chat_line`
单一分发,只有一份来源。默认 `chat_callback=None` 且无订阅者 = 不回调。
单个订阅者抛错会被隔离,不影响其它订阅者。接入方跑完应 `unsubscribe_chat_line(...)`
收尾(Simulator 挂着插件时,`plugin_teardown()` 会自动退订)。

### 8. 通用插件面:`Plugin` / `PluginManager`

mavis 自己不认识任何具体插件,但任何外部包都能挂进来。插件接口:

```
from mavisframework.plugin import Plugin, PluginManager

class MyPlugin(Plugin):
    name = "my"                      # 标识,错误隔离与日志用
    def setup(self, ctx=None): ...   # 挂进运行上下文时调一次(ctx 含 game/config/simulator)
    def on_event(self, evt): ...     # 收到一个协议事件(agent / time / story / chat_line)
    def teardown(self): ...          # 运行收尾时调一次(关连接 / 刷盘)
```

三个方法都**可选**实现,缺省是 no-op。**推荐形态:直接传插件实例列表**——
实例天然带着自己的配置(带必传配置的插件只能用实例;用名字 `create("town")` 会因缺参
抛错,入口点发现也会跳过它)。

```
sim = Simulator(..., plugins=[plugin_a, plugin_b])   # 不传 = 完全不介入
...
sim.plugin_teardown()   # 显式收尾(on_event 后插件立刻收到 agent/time/story/chat_line)
```

`PluginManager` 还提供可选的**按名注册表 + 入口点发现**:组名 `mavisframework.plugins`
在外部包的 `pyproject.toml` 声明即被发现。发现只对"声明可无参构造"的插件生效——
带必传配置的插件因签名含必需参数会被跳过,需调用方显式 `create(name, **配置)` 实例化。

生命周期:随 `Game` / `Simulator` 的创建与收尾调用;`setup` 在首次 `simulate` 惰性挂载,
`teardown` 由调用方显式触发。故障隔离:单个插件在 `setup` / `on_event` / `teardown`
抛异常只记 warning,不打断其它插件,也不打断主循环。

**事件边界与只读约定**:插件总线上 `time` / `chat_line` / `story` 三种事件的键名与
`mavisframework.runtime.protocol` 一致。`agent` 事件是**框架原生子集**,字段为
`name` / `coord` / `path` / `time`,外加一个原始 `state`(= 该角色 `config["agents"]`
里的原样引用,内含 `action` / `location` / `currently` 等);协议顶层要求的
`action`(字符串)等字段由消费方自行映射(`mavis-vizkit.events.as_text()` 就是这种
映射),框架不在总线上做协议级转换——否则等于把可视化侧的收敛逻辑复制进框架,
违反"不复制实现"。另外,总线上的事件 dict 与 `on_*` 回调、`config["agents"]` 可能
共用同一引用,framework 在后续步骤会继续改动其中的 dict;插件应**只读**事件、不要
原地修改,也不要长期持有当作稳定快照。

## 三、新增能力的两条硬约定

1. **默认关闭**:新增的可选能力一律默认不生效(参数默认 `None`、开关默认 `False`、
   字段默认空串)。不配置就必须与历史行为逐位一致。
2. **零业务词汇**:框架源码里不出现任何接入方的业务词(case01、角色名、公司名等)。
   通用演示词汇(如"投资顾问"这类场景角色名)不算越界。

两条都有测试兜底:`tests/test_extension_surface.py`。

## 四、接入方遇到新需求时的顺序

1. **配置能不能解决?**(换 agent.json / maze.json / story.json / 参数)
2. **已存在的扩展点能不能解决?**(上面的清单)
3. **接入方自己能不能解决?**——多数情况下可以。例:mavis 的随机数走全局 `random`
   模块且没有 seed 参数,接入方只要在自己进程里 `random.seed(n)` 就能控制大部分随机,
   不需要改框架;再例:实验节奏完全由接入方逐步调用 `simulate(step=1)` 决定,框架不抢轮次。
4. **都不行,才考虑新增一个通用扩展点**(纯新增、默认关闭、语义中立、带单测)。
5. **需要改动现有行为语义 → 不做。**宁可在接入方绕一下,也不动既有语义;
   这条是 mavis 对外承诺的基础。

## 五、已知限制(如实写,别当承诺)

- **随机数无 seed 参数**:框架用全局 `random`,`Agent.think` 等走线程池并行,
  跨角色消费随机的顺序不确定。接入方 `random.seed()` 能收敛但**做不到逐样本可复现**。
- **私有成员不在契约内**:`agent._timer`、`agent._governance`、`associate._index` 这类
  下划线成员不保证稳定。当前 provenance 的实时服务在读 `_timer` / `_governance`,
  case01 的隔离探针在读 `associate.memory` / `_index`,这些属于**待收编**的越界用法:
  要么补成公开读侧,要么把语义写进文档后再依赖。
- **`Game` 的惰性 LLM provider**:provider 在 `Agent.reset()` 里创建,构造 `Game` 后
  若不调用一次 `reset_game()`,首次使用时报"缺少可用 LLM"。这是已知行为,不要依赖
  "构造完就能直接跑"。

## 六、改框架时的检查清单

1. 跑 `pytest tests`(含契约测试),全绿。
2. 确认新增内容默认关闭:不配置时逐位一致。
3. 确认源码零业务词汇。
4. 若是新增扩展点,更新本文件与 README 的扩展面小节。
5. 若是修 bug,补一条能复现该 bug 的测试。
