# mavisframework — 自研生成式智能体仿真框架

**定位**:面向"精细化业务推演"的生成式智能体仿真框架。
Agent 在空间里生活、记忆、反思、决策、交互,每一步可配置、可解释、可实时可视化。

**硬约束**:框架层零渲染依赖(不嵌 Phaser/Unity/Flask)——前端只是"消费协议消息的壳"。

---

## 安装(uv 推荐)

```bash
# 方式 A:从源码构建 wheel 并安装(推荐,已验证稳定)
uv build
uv pip install dist/mavisframework-1.0.0-py3-none-any.whl

# 方式 B:可编辑安装(开发框架时改代码即时生效)
uv venv --python 3.12
uv pip install -e .
```

> ⚠️ **已知问题**:当前环境下 editable 安装(`-e`)存在 import 怪癖——顶层 `mavisframework`
> 可导入,但切换工作目录后嵌套子模块(如 `mavisframework.config.loader`)可能解析失败。
> 生产/平台集成请用方式 A(wheel 安装)。
>
> 依赖仅 `pydantic>=2.0`、`requests>=2.31`(无任何 AI/渲染框架硬依赖);
> Python ≥ 3.12。LLM 通过可插拔 Provider(Ollama/OpenAI)接入,不强制。

## 分层

```
mavisframework/
├── core/                 # 纯逻辑层(零渲染/通信依赖)
│   ├── event.py          # 事件模型(世界最小原子)
│   ├── action.py         # 行动(Action,时间注入)
│   ├── spatial.py        # 空间记忆(地址树)
│   ├── schedule.py       # 日程(时间注入)
│   ├── timer.py          # 模拟时钟(可注入,零全局状态)
│   ├── memory.py         # 联想记忆 + 三因子检索(近因0.995/重要/相关)
│   ├── store.py          # 记忆存储抽象(SimpleStore 纯 stdlib / LlamaIndexStore 向量)
│   ├── associate.py      # 联想记忆(事件/对话/想法 + 检索)
│   ├── agent_core.py     # Agent 完整生命周期(组件注入式:LLM/记忆/空间/提示词/时钟)
│   └── prompts/          # 提示词模板(29 个 .txt,随包分发)
├── scene/
│   └── maze.py           # 空间/碰撞/寻路/地址索引(纯标准库)
├── runtime/
│   ├── protocol.py       # ★ 消息协议(前端/Unity/决策平台统一消费的契约)
│   ├── llm.py            # LLM 适配接口(可插拔:Ollama/OpenAI)
│   ├── llm_providers.py  # Provider 实现(自包含,零 modules 依赖)
│   ├── game.py           # 游戏容器(创建 agents + maze + conversation)
│   ├── simulator.py      # 并行调度 + 回调 + 存档 + 决策导出(与前端解耦)
│   └── compressor.py     # 实时压缩器(逐步生成 Agent 状态/回放帧)
├── output/
│   └── decisions.py      # 决策事件导出(供决策平台/专家界面)
└── config/
    ├── loader.py         # 场景配置 + 模拟配置加载(personas/新开/续跑)
    └── validator.py      # 配置校验(语法/地图一致性/角色交叉)
```

## 环境变量(路径注入)

| 变量 | 默认 | 用途 |
|---|---|---|
| `MAVIS_PROMPT_DIR` | 包内 `prompts/` | 提示词模板目录 |
| `MAVIS_CONFIG_PATH` | `data/config.json` | agent_base 配置(LLM 等) |
| `MAVIS_ASSETS_ROOT` | `assets/village` | 静态资源相对根 |
| `MAVIS_STATIC_ROOT` | `frontend/static` | 前端静态资源根(compressor) |
| `MAVIS_CHECKPOINTS_ROOT` | `results/checkpoints` | 存档根目录 |

## 与业务层/前端层的关系

```
scenarios/          业务层(换业务=改配置):角色/场景/关系/剧情
   ↓ 加载
mavisframework/     框架层(纯逻辑,零渲染)
   ↓ 产出
runtime/protocol.py 消息协议(agent/time/chat_line/decision...)
   ↓ 消费
frontend/phaser     前端壳(现在,浏览器)
frontend/unity      前端壳(将来,WebSocket 消费同一协议)
决策平台            消费 DecisionEventStream
```

## 关键契约(runtime/protocol.py)

| 消息 | 用途 | 消费者 |
|---|---|---|
| `AgentState` | 单 agent 状态(坐标/路径/动作) | Phaser/Unity |
| `TimeMsg` | 模拟时间 | 前端时钟 |
| `ChatLineMsg` | 对话逐句 | 对话面板 |
| `SnapshotMsg` | 全量快照 | 新连接追赶 |
| `DecisionEvent` | 决策事件 | 决策平台/专家界面 |

**坐标一律格子坐标;消息传输无关(SSE/WebSocket 都可)。**

## 使用方式(1 条路线)

### 框架驱动 ✅ 已落地
框架 `Game` + `Simulator` + `LiveCompressor` 驱动完整模拟(并行思考/存档/决策导出/WebSocket 推送),项目已无 `modules/` 旧实现,全部逻辑在框架内。投资场景 5 角色从零跑通验证。

> 完整演示平台见 [Provenance](https://github.com/hellobs/provenance):实时服务
> `live_fastapi.py` 即框架路线的参考实现(FastAPI + WebSocket 消费框架契约消息)。
> 旧实现(start.py/live.py/compress.py/replay.py + modules/)已移除,可在 git 历史回退。

## Unity 迁移(框架视角)

```
框架核心(agent/记忆/寻路/决策导出)  ← 零改动
        ↓ protocol.py 消息
传输:SSE(Phaser) → WebSocket(Unity)   ← 只换传输层
前端:Phaser → Unity                    ← 只换渲染层(消费同一协议)
```

**框架层不感知前端是什么——这就是"Phaser 不嵌入框架"的保证。**

## 状态

- ✅ 已完成:protocol / core(event,memory,agent_core) / scene(maze) / runtime(llm,simulator) / output(decisions) / config(loader) / scenarios(investment 示例)
- ✅ 框架独立运行:Agent 完整生命周期(思考/日程/感知/反应/对话/反思)、记忆存储(SimpleStore 纯 stdlib / LlamaIndexStore 向量可选)、提示词系统全部迁入 framework,不依赖 modules
- ✅ 平台消费:Provenance 平台的实时服务(live_fastapi.py)由框架 Game + Simulator 驱动(FastAPI + WebSocket),决策导出(decisions.json)接入管线
- ⏳ 后续:业务层配置生效(关系注入/剧情注入)、Unity 前端

## 仓库结构

```
mavisframework/          # ★ 框架包(pip 包,pyproject 在根)
├── core/ scene/ runtime/ output/ config/ prompt/
└── prompts/             # 提示词模板(随包分发)
config_tool/             # ★ 角色配置工具(独立 FastAPI 服务,填表单生成角色 JSON)
pyproject.toml           # 包构建配置(uv build / uv pip install -e .)
```

**config_tool 说明**:配置工具属框架仓库,但产物(角色/关系/剧情)写入平台的前端资源与场景目录。默认指向兄弟目录 `../provenance`(本地开发时 mavis 与 provenance 仓库并列);部署时通过环境变量覆盖:
- `MAVIS_ASSETS_ROOT` — 平台前端资源根(`frontend/static/assets/village`)
- `MAVIS_SCENARIOS_DIR` — 平台场景目录(`scenarios`)
