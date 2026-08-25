# 教程:运行时(Game 与 Simulator)

本教程带你运行一次真实的模拟——从 `Game` 容器创建角色,到 `Simulator` 驱动完整生命周期。
读完你会理解:角色如何被实例化、LLM 如何接入、以及如何跑一步完整模拟。

## 1. Game:游戏容器

`Game` 持有全部 Agent 与地图,是模拟的"舞台"。构造它需要三样东西:配置、静态资源根、存档对话。

```python
import mavisframework as mf

# 1) 准备配置(见"配置教程")
names = ["沈砚之", "苏清越", "陈慕白", "林晚晴", "老周"]
cfg = mf.load_config("20250213-09:30", 2, names)

# 2) 构造 Game
game = mf.Game(
    name="demo",
    static_root="frontend/static",   # 静态资源根(角色/地图所在)
    config=cfg,
    conversation={},                  # 存档对话(新模拟为空)
    timer=mf.Timer(start=cfg["time"]["start"]),
)
game.reset_game()                     # 重置到初始状态

# 3) 查看角色
for name, agent in game.agents.items():
    print(name, agent.coord, agent.scratch.currently)
```

`static_root` 会拼到 `cfg` 里的相对路径上:`frontend/static` + `assets/village/...`
→ 实际文件 `frontend/static/assets/village/...`。这就是为什么配置里用相对路径。

## 2. 让一个 Agent 思考

`game.agent_think(name, status)` 驱动单个 Agent 完成一步思考(移动 + 日程 + LLM 输出):

```python
status = {"coord": game.agents["老周"].coord}   # 当前坐标
result = game.agent_think("老周", status)
result["plan"]   # 思考结果(日程/动作计划)
result["info"]   # Agent 状态快照(坐标/动作/对话)
```

> 需要 LLM:Agent 的思考依赖 LLM(日程生成、行动描述)。未配置 LLM 时框架会
> 抛出明确错误提示配置大模型——见"配置大模型"一节。

## 3. 配置大模型

LLM 通过可插拔 Provider 接入,配置在 `data/config.json` 的 `agent.think.llm`:

```json
{
  "agent": {
    "think": {
      "llm": {
        "provider": "ollama",
        "model": "qwen3:4b-instruct-2507-q4_K_M",
        "base_url": "http://127.0.0.1:11434/v1",
        "api_key": ""
      }
    }
  }
}
```

**Provider 二选一:**

| provider | 说明 | 示例 model |
|---|---|---|
| `ollama` | 本地免费,推荐开发 | `qwen3:4b-instruct-2507-q4_K_M` |
| `openai` | 任意 OpenAI 兼容 API | `deepseek-chat`(base_url 指到 DeepSeek) |

程序内创建 Provider:

```python
llm = mf.create_llm_provider(cfg["agent_base"]["think"]["llm"])
print(llm.is_available())   # True = 已连接
```

## 4. Simulator:并行调度器

`Simulator` 驱动全部 Agent 的并行思考、对话、存档与剧情注入。构造后调用 `simulate` 跑指定步数:

```python
# 加载业务配置(关系注入 Agent,剧情注入 Simulator)
import json
rels = json.load(open("scenarios/investment/relationships.json", encoding="utf-8")).get("relations", [])
story = json.load(open("scenarios/investment/story.json", encoding="utf-8")).get("events", [])

sim = mf.Simulator(
    max_workers=max(1, len(game.agents)),  # 并发数=角色数
    export_decisions=False,                 # 是否导出决策事件流
    story=story,                            # 剧情事件(危机注入)
    on_story=lambda ev: print("剧情:", ev),  # 剧情触发回调
)

sim.simulate(
    game,
    cfg,
    step=1,                  # 跑 1 步
    stride=2,
    start_step=0,
    checkpoints_folder="results/checkpoints/demo",  # 存档目录
    on_step=lambda *a: None,   # 每步完成回调
    on_agent=lambda *a: None,  # 每 Agent 完成回调
)
```

**跑完看结果**:存档目录生成 `simulate-<时间>.json`(每个 Agent 的状态/动作/对话)
和 `conversation.json`(对话记录)。

## 5. 完整示例:跑一步模拟

```python
import json
import mavisframework as mf

names = ["沈砚之", "苏清越", "陈慕白", "林晚晴", "老周"]
cfg = mf.load_config("20250213-09:30", 2, names)

game = mf.Game("demo", "frontend/static", cfg, {},
               timer=mf.Timer(start=cfg["time"]["start"]))
game.reset_game()

story = json.load(open("scenarios/investment/story.json", encoding="utf-8")).get("events", [])
sim = mf.Simulator(max_workers=2, export_decisions=False, story=story)

sim.simulate(game, cfg, step=1, stride=2, start_step=0,
             checkpoints_folder="results/checkpoints/demo")

print("完成:1 步模拟,存档在 results/checkpoints/demo")
```

## 小结

- `Game` 是舞台:持有 Agent + 地图;`game.agent_think` 驱动单 Agent 思考
- LLM 必须配置(ollama 或 openai 兼容),否则框架明确报错
- `Simulator.simulate` 并行驱动全部角色,自动存档与剧情注入
- 存档是"断点续跑"的基础(`load_config_from_log`)

下一步:阅读 [消息协议教程](tutorial-protocol.md),了解前端/Unity 如何消费模拟输出。
