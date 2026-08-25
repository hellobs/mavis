# 教程:配置加载与校验

本教程带你从零掌握 mavisframework 的**配置系统**——如何准备角色/场景配置、如何加载、
以及如何在校验失败时获得清晰的错误信息。读完你会理解"业务层配置 → 框架"的完整链路。

## 配置的三个层次

mavisframework 将配置分成三层,职责分离:

| 层 | 内容 | 谁维护 |
|---|---|---|
| **业务层**(scenarios/) | 角色(agent.json)、场景(maze.json)、关系(relationships.json)、剧情(story.json) | 业务方/配置工具 |
| **模拟配置** | LLM 选择、stride、起始时间、agents 清单 | 启动代码 |
| **agent_base 配置** | 感知/日程/思考的默认参数(在 `data/config.json`) | 开发方 |

换业务 = 换 scenarios/ 目录,框架零改动——这是框架的设计核心。

## 1. 角色配置(agent.json)

一个最小角色(字段可由配置工具生成):

```json
{
  "name": "老周",
  "coord": [10, 6],
  "currently": "正在盯着今天的行情",
  "scratch": {
    "age": 60,
    "innate": "谨慎、情绪化",
    "learned": "多年炒股经验",
    "lifestyle": "早起看盘",
    "daily_plan": "开盘前到资料室看行情"
  },
  "spatial": {
    "address": {"living_area": ["the Ville", "投资咨询中心", "资料室"]},
    "tree": {"the Ville": {"投资咨询中心": {"资料室": ["办公桌"]}}}
  }
}
```

> 注意:`spatial.address.living_area` 与 `tree` 的地址**必须带 world 前缀**
> (`the Ville`),且树中叶子地址必须存在于地图(maze.json)。

## 2. 加载模拟配置(load_config)

使用顶层 API 的 `load_config` 创建一次新模拟的配置:

```python
import mavisframework as mf

cfg = mf.load_config(
    start_time="20250213-09:30",   # 起始模拟时间
    stride=2,                       # 每步游戏分钟数
    agents=["沈砚之", "老周"],      # 参与角色
)
```

返回的 `cfg` 包含:

```python
cfg["stride"]          # 2
cfg["time"]["start"]   # "20250213-09:30"
cfg["agents"]          # {"沈砚之": {"config_path": "assets/village/agents/沈砚之/agent.json"}, ...}
cfg["agent_base"]      # 来自 data/config.json 的默认参数(感知/日程/思考/LLM)
cfg["maze"]["path"]    # "assets/village/maze.json"
```

**配置来源(路径注入)**:`load_config` 通过环境变量定位两个文件:

| 环境变量 | 默认值 | 用途 |
|---|---|---|
| `MAVIS_CONFIG_PATH` | `data/config.json` | agent_base 默认参数(含 LLM 选择) |
| `MAVIS_ASSETS_ROOT` | `assets/village` | 静态资源(角色/地图)相对根 |

### 断点续跑(load_config_from_log)

从上次的存档恢复模拟——时间会自动推进一个 stride:

```python
cfg = mf.load_config_from_log("results/checkpoints/invest-live")
# 存档里是 09:30,stride=2 → cfg["time"]["start"] == "20250213-09:32"
```

## 3. 校验配置(validate_all)

配置是给 LLM 和地图引擎吃的,错了会运行崩溃。用 `validate_all` 提前拦截:

```python
import json, glob
import mavisframework as mf

# 加载全部角色
agents = {}
for f in glob.glob("frontend/static/assets/village/agents/*/agent.json"):
    d = json.load(open(f, encoding="utf-8"))
    agents[d["name"]] = d

# 加载关系与剧情(可为空)
rels = json.load(open("scenarios/investment/relationships.json", encoding="utf-8")).get("relations", [])
story = json.load(open("scenarios/investment/story.json", encoding="utf-8")).get("events", [])

# 加载地图
maze = json.load(open("frontend/static/assets/village/maze.json", encoding="utf-8"))

errors = mf.validate_all(agents, rels, story, maze)
if errors:
    print("配置有问题:")
    for e in errors:
        print("  -", e)
else:
    print("配置全部通过")
```

**校验覆盖**:

| 检查项 | 说明 |
|---|---|
| 语法层 | 必填字段存在、字段类型正确(如 `coord` 必须是 `[x, y]` 数组) |
| 地图一致性 | `coord` 在地图范围内;`spatial` 树地址存在于地图 |
| 角色交叉 | 关系/剧情引用的角色在 agents 中存在;剧情 `time` 是合法 `HH:MM`(00:00-23:59);`importance` 在 1-10 |

**错误示例**(故意改坏配置):

```python
bad_agent = dict(agents["老周"])
bad_agent["coord"] = [999, 999]   # 超出地图

errors = mf.validate_agents({"老周": bad_agent}, maze)
print(errors)
# ['[agent:老周].coord [999, 999] 超出地图范围 [30, 20]']
```

## 4. 场景配置(ScenarioConfig)

`load_scenario` 一次性加载某个业务场景目录的全部配置:

```python
scenario = mf.load_scenario("scenarios/investment", validate=True)
# validate=True 时,加载后立即校验,失败抛 ValueError

scenario.agents          # {name: agent_json}
scenario.maze            # maze.json 内容
scenario.relationships   # relations 列表
scenario.story           # events 列表
```

## 小结

- 配置分三层:业务层 / 模拟配置 / agent_base 默认参数
- `load_config` 建新模拟、`load_config_from_log` 断点续跑
- `validate_all` / `validate_agents` / `validate_relationships` / `validate_story` 分层校验
- 地址必须带 world 前缀;剧情 time 必须合法 `HH:MM`

下一步:阅读 [运行时教程(Game 与 Simulator)](tutorial-game.md),把配置跑起来。
